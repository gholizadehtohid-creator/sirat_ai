/* Dashboard wiring: read the controls, fetch the API, render charts + tables.

   The page has no per-symbol demo section any more. It used to open with five
   hard-coded shares in depth, which put the least representative numbers on the
   page in the most prominent position. Every view here is driven by the whole
   universe, and the four analyses the user reads in sequence -- شاخص کل,
   شاخص کل هم‌وزن, تحلیل بنیادی صنایع, تحلیل تکنیکال صنایع -- each own a
   section rather than sharing one card. */

import {
  hBarChart, stackedHBar, lineChart, divergingBar,
  seriesColors, fmt, hideTip
} from "./charts.js";

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
  fundamentals: null, codal: null, technical: null
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

    throw new Error(message);
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

/* ---------- indices, one section each ---------- */

function renderIndexCard(index, ids, rep) {
  if (!index || index.error) {
    banner(ids.status, ids.statusText, "is-error",
      `<strong>داده‌ی این شاخص موجود نیست.</strong> ${esc(index?.error || "")}` +
      bootstrapNote(rep));
    tiles(ids.kpis, []);
    $(ids.chart).innerHTML = '<div class="empty">داده‌ای نیست</div>';
    if ($(ids.table)) $(ids.table).innerHTML = "";
    if (ids.breakdown) $(ids.breakdown).innerHTML = "";
    return;
  }

  const t = index.technical || {};
  const r = index.returns || {};

  banner(ids.status, ids.statusText, "is-live",
    `<strong>${esc(index.name)}</strong> — ${index.bars} جلسه تا ${index.last_date}. ` +
    `مؤلفه‌ی حجم از ${esc(index.volume_source || "هیچ منبعی")} تغذیه شده.`);

  tiles(ids.kpis, [
    { k: "مقدار شاخص", v: num(index.close, 0), sub: index.last_date },
    { k: "امتیاز تکنیکال", v: t.scored === false ? "—" : t.score, sub: badge(t.status || "NO DATA") },
    { k: "روند", v: t.trend || "n/a", sub: `RSI ${num(t.rsi, 1)}` },
    { k: "۲۰ روزه", v: signed(r["20d"]), sub: "بازده" },
    { k: "۶۰ روزه", v: signed(r["60d"]), sub: "بازده" }
  ]);

  const s = index.series || {};

  const points = (s.dates || []).map((d, k) => ({
    date: d, close: s.close[k], ma_fast: s.ma_fast[k], ma_slow: s.ma_slow[k]
  }));

  $(ids.chartHint).textContent =
    `${index.name} — بسته در برابر میانگین‌های متحرک.`;

  lineChart($(ids.chart), {
    data: points,
    series: [
      { key: "close", label: "Close" },
      { key: "ma_fast", label: "Fast MA" },
      { key: "ma_slow", label: "Slow MA" }
    ],
    format: (v) => fmt(v, 0)
  });

  const colors = seriesColors(3);
  $(ids.legend).innerHTML = ["Close", "Fast MA", "Slow MA"]
    .map((l, k) => `<span class="key"><i style="background:${colors[k]}"></i>${l}</span>`)
    .join("");

  if (ids.breakdown && t.breakdown) {
    hBarChart($(ids.breakdown), {
      data: COMPONENTS.map(c => ({ label: c, value: t.breakdown[c] ?? 0 })),
      max: 25,
      format: (v) => fmt(v, 0)
    });
  }

  if ($(ids.table)) {
    $(ids.table).innerHTML = table(
      ["بازه", "بازده"],
      [["۱ روزه", signed(r["1d"])], ["۵ روزه", signed(r["5d"])],
       ["۲۰ روزه", signed(r["20d"])], ["۶۰ روزه", signed(r["60d"])]]
    );
  }
}

function renderIndices(rep) {
  state.indices = rep;

  const list = rep.indices || [];

  const cap = list.find(i => i.weighting === "capitalisation");
  const equal = list.find(i => i.weighting === "equal");

  renderIndexCard(cap, {
    status: "capIndexStatus", statusText: "capIndexStatusText",
    kpis: "capIndexKpis", chart: "capIndexChart", chartHint: "capIndexChartHint",
    legend: "capIndexLegend", table: "capIndexTable", breakdown: "capIndexBreakdown"
  }, rep);

  renderIndexCard(equal, {
    status: "ewIndexStatus", statusText: "ewIndexStatusText",
    kpis: "ewIndexKpis", chart: "ewIndexChart", chartHint: "ewIndexChartHint",
    legend: "ewIndexLegend", table: "ewIndexTable"
  }, rep);

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

function renderTechnical(rep) {
  state.technical = rep;

  const clear = () => {
    ["technicalPriceChart", "technicalMomentumChart"].forEach(id => {
      const n = $(id);
      if (n) n.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    });
    $("technicalTable").innerHTML = "";
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
    `این بخش داده است، نه سیگنال.`);

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
      sub: `ADX ${n(L.adx, 1)}` }
  ]);

  const series = rep.series || [];

  const priceNode = $("technicalPriceChart");
  if (priceNode) {
    if (series.length) {
      lineChart(priceNode, {
        data: series, xKey: "date",
        series: [
          { key: "close", label: "بسته" },
          { key: "ma_fast", label: "کوتاه" },
          { key: "ma_slow", label: "بلند" },
          { key: "bb_upper", label: "باند بالا" },
          { key: "bb_lower", label: "باند پایین" }
        ]
      });
    } else {
      priceNode.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    }
  }

  const momNode = $("technicalMomentumChart");
  if (momNode) {
    if (series.length) {
      lineChart(momNode, {
        data: series, xKey: "date",
        series: [
          { key: "macd", label: "MACD" },
          { key: "macd_signal", label: "سیگنال" },
          { key: "rsi", label: "RSI" }
        ]
      });
    } else {
      momNode.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    }
  }

  // A compact current-reading table beneath the charts.
  const rows = [
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
    ["ADX", n(L.adx, 1)],
    ["حجم", L.volume === null ? "—" : money(L.volume)],
    ["میانگین حجم", L.volume_ma === null ? "—" : money(L.volume_ma)]
  ];

  $("technicalTable").innerHTML = table(
    ["اندیکاتور", "مقدار"], rows.map(r => [r[0], r[1]]));
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
    ["technicalPriceChart", "technicalMomentumChart"].forEach(id => {
      const node = $(id);
      if (node) node.innerHTML = '<div class="empty">داده‌ای نیست</div>';
    });
    $("technicalTable").innerHTML = "";
    tiles("technicalKpis", []);
    console.error(err);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ تحلیل"; }
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
    banner("screenStatus", "screenStatusText", "is-error",
      `<strong>غربال شکست خورد.</strong> ${esc(err.message)}`);
    setTask("screen", "failed", err.message);
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
    $("correlationTable").innerHTML = "";
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
    $("correlationTable").innerHTML = "";
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

  $("riskHint").textContent = riskRep.price_band_warning || "";

  if (corr.labels && corr.values) {
    const cells = corr.values.map((row, i) => [
      `<strong>${corr.labels[i]}</strong>`,
      ...row.map((v, j) => {
        if (i === j) return "—";
        if (v === null) return "n/a";
        const cls = v >= 0.7 ? "down" : v <= 0.3 ? "up" : "";
        return `<span class="${cls}">${v.toFixed(2)}</span>`;
      })
    ]);
    $("correlationTable").innerHTML = table(["", ...corr.labels], cells);
  } else {
    $("correlationTable").innerHTML =
      '<div class="empty">همبستگی محاسبه نشد — سابقه‌ی مشترک کافی نیست</div>';
  }

  if (riskRep.notes && riskRep.notes.length) {
    $("corrHint").textContent = riskRep.notes.join("  ");
  }
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
    banner("portfolioStatus", "portfolioStatusText", "is-error",
      `<strong>ساخت سبد شکست خورد.</strong> ${esc(err.message)}`);
    setTask("portfolio", "failed", err.message);
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
    const cls = ["running", "done", "failed"].includes(s.state) ? s.state : "queued";
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
async function loadAll() {
  loadRegime();
  loadIndices();
  loadSectors();
  loadFundamentals();
  loadMoneyflow();
  await loadScreen();
  await loadPortfolio();
}

function rerender() {
  if (state.regime) renderRegime(state.regime);
  if (state.indices) renderIndices(state.indices);
  if (state.sectors) renderSectors(state.sectors);
  if (state.fundamentals) renderFundamentals(state.fundamentals);
  if (state.codal) renderRoutes(state.codal.routes, state.codal.coverage);
  if (state.flow) renderMoneyflow(state.flow);
  if (state.technical) renderTechnical(state.technical);
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

on("runIndicesBtn", "click", () => loadIndices(true));
on("runSectorsBtn", "click", () => loadSectors(true));
on("runFundBtn", "click", () => loadFundamentals(true));
on("runFlowBtn", "click", () => loadMoneyflow(true));
on("runRegimeBtn", "click", () => loadRegime(true));
on("runPortfolioBtn", "click", () => loadPortfolio(true));
on("runTechnicalBtn", "click", () => loadTechnical(true));

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

getJSON("/api/status").then(status => {
  $("footnote").textContent =
    `${status.total_bars.toLocaleString()} کندل ذخیره‌شده` +
    (status.date_min ? ` از ${status.date_min} تا ${status.date_max}` : "") +
    ` · سرمایه‌ی پیش‌فرض ${status.config.equity.toLocaleString()} ${status.config.currency}` +
    ` · ریسک هر موقعیت ${status.config.risk_per_trade_pct}%`;
}).catch(() => {});

loadAll();
