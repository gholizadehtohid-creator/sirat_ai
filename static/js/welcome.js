// The welcome page (templates/welcome.html, 2026-11-26). A module of its own:
// the panel's CSP allows no inline script.

const PANEL = "/panel";

// A link meant for the panel itself (a #tab, ?engine=, ?tech=...) goes
// straight on: the welcome page is the front door, not a wall.
if (location.hash.length > 1 || location.search.length > 1) {
  location.replace(PANEL + location.search + location.hash);
}

const $ = id => document.getElementById(id);
const root = document.documentElement;

// The same theme key as the panel, so the two always match.
const savedTheme = localStorage.getItem("sirat-theme");
if (savedTheme) root.setAttribute("data-theme", savedTheme);
$("wTheme").addEventListener("click", () => {
  const dark = root.getAttribute("data-theme")
    ? root.getAttribute("data-theme") === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  root.setAttribute("data-theme", next);
  localStorage.setItem("sirat-theme", next);
  if (lastSeries) drawSpark(lastSeries);
});

const faNum = (v, digits = 0) => (v === null || v === undefined || Number.isNaN(Number(v)))
  ? "—" : new Intl.NumberFormat("fa-IR", { maximumFractionDigits: digits,
                                            minimumFractionDigits: digits }).format(Number(v));
const faDigits = s => String(s || "").replace(/\d/g, d => "۰۱۲۳۴۵۶۷۸۹"[d]);
const jDate = iso => {
  if (!iso) return "";
  const d = new Date(String(iso).slice(0, 10) + "T12:00:00");
  return Number.isNaN(d.getTime()) ? faDigits(iso) : new Intl.DateTimeFormat("fa-IR-u-ca-persian",
    { year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
};
const move = (pct, digits = 2) => {
  if (pct === null || pct === undefined) return "";
  const cls = pct > 0 ? "w-up" : pct < 0 ? "w-down" : "";
  const arrow = pct > 0 ? "▲" : pct < 0 ? "▼" : "•";
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `<span class="${cls}">${arrow} ${sign}${faNum(Math.abs(pct), digits)}٪</span>`;
};

// Built from its parts: the joined form came out as «۱۴۰۵ آذر ۵, پنجشنبه»
// in Edge.
{
  const parts = Object.fromEntries(new Intl.DateTimeFormat("fa-IR-u-ca-persian",
    { weekday: "long", day: "numeric", month: "long", year: "numeric" })
    .formatToParts(new Date()).map(p => [p.type, p.value]));
  $("wToday").textContent = `${parts.weekday} ${parts.day} ${parts.month} ${parts.year}`;
}

// Into the panel: the button, or Enter anywhere on the page.
function enter(href = PANEL) {
  $("wEnter").classList.add("is-leaving");
  location.href = href;
}
$("wEnter").addEventListener("click", ev => { ev.preventDefault(); enter(); });
document.addEventListener("keydown", ev => {
  if (ev.key === "Enter" && !ev.altKey && !ev.ctrlKey && !ev.metaKey &&
      !(ev.target.closest && ev.target.closest("a, button, input, textarea, select"))) {
    enter();
  }
});

// ---- the index's last sessions, with a hover read-out ----
let lastSeries = null;

function drawSpark(series) {
  lastSeries = series;
  const box = $("wSpark");
  const w = Math.max(box.clientWidth, 120), h = box.clientHeight || 64;
  const pad = 4;
  const ys = series.map(p => p[1]);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const x = i => pad + (w - 2 * pad) * i / Math.max(series.length - 1, 1);
  const y = v => h - pad - (h - 2 * pad) * (hi === lo ? 0.5 : (v - lo) / (hi - lo));
  const pts = series.map((p, i) => `${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`);
  const accent = getComputedStyle(document.body).getPropertyValue("--accent").trim() || "#2563d6";
  box.innerHTML =
    `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="شاخص کل در ${faNum(series.length)} جلسه‌ی اخیر">` +
    `<defs><linearGradient id="wArea" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="${accent}" stop-opacity="0.28"/>` +
    `<stop offset="1" stop-color="${accent}" stop-opacity="0"/></linearGradient></defs>` +
    `<path class="area" fill="url(#wArea)" d="M${pts[0]} L${pts.join(" L")} L${x(series.length - 1).toFixed(1)},${h} L${x(0).toFixed(1)},${h} Z"/>` +
    `<polyline class="line" points="${pts.join(" ")}"/>` +
    `<line class="guide" id="wGuide" x1="0" x2="0" y1="0" y2="${h}" visibility="hidden"/>` +
    `<circle class="dot" id="wDot" r="4" cx="${x(series.length - 1)}" cy="${y(ys[ys.length - 1])}"/>` +
    `</svg>`;
  const svg = box.querySelector("svg");
  const read = $("wSparkRead");
  const show = i => {
    const p = series[i];
    $("wDot").setAttribute("cx", x(i)); $("wDot").setAttribute("cy", y(p[1]));
    const g = $("wGuide");
    g.setAttribute("x1", x(i)); g.setAttribute("x2", x(i)); g.setAttribute("visibility", "visible");
    read.innerHTML = `${jDate(p[0])}: <strong>${faNum(p[1])}</strong>`;
  };
  const reset = () => {
    const i = series.length - 1;
    $("wDot").setAttribute("cx", x(i)); $("wDot").setAttribute("cy", y(series[i][1]));
    $("wGuide").setAttribute("visibility", "hidden");
    read.textContent = `${faNum(series.length)} جلسه‌ی اخیر — نشانگر را روی نمودار ببرید`;
  };
  svg.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect();
    const i = Math.round((ev.clientX - r.left - pad) / (r.width - 2 * pad) * (series.length - 1));
    show(Math.min(Math.max(i, 0), series.length - 1));
  });
  svg.addEventListener("pointerleave", reset);
  reset();
}
addEventListener("resize", () => { if (lastSeries) drawSpark(lastSeries); });

// ---- today's figures ----
fetch("/api/welcome").then(r => r.json()).then(d => {
  const ix = d.index;
  if (ix) {
    $("wIndexValue").textContent = faNum(ix.close);
    $("wIndexChange").innerHTML = `${move(ix.change_pct)} <span>نسبت به جلسه‌ی قبل</span>`;
    $("wIndexDate").textContent = jDate(ix.date);
    if ((ix.series || []).length > 2) drawSpark(ix.series);
    $("wAsOf").textContent = `آخرین جلسه‌ی ثبت‌شده: ${jDate(ix.date)}`;
  }
  const fx = d.dollar;
  if (fx) {
    $("wDollarValue").innerHTML = `${faNum(fx.close)} <small class="w-when">ریال</small>`;
    $("wDollarChange").innerHTML = `${move(fx.change_1w == null ? null : fx.change_1w * 100, 1)} <span>در یک هفته</span>`;
    $("wDollarDate").textContent = faDigits(fx.date);
    if (fx.nima) {
      $("wDollarMore").innerHTML = `مرکز مبادله (نیما): <b>${faNum(fx.nima)}</b> ریال`;
    }
  }
  const v = d.valuation;
  if (v) {
    $("wPeValue").textContent = faNum(v.pe, 1);
    $("wPeDate").textContent = jDate(v.date);
    if (v.percentile != null) {
      const word = v.percentile >= 80 ? "گران‌تر از معمول" : v.percentile <= 20 ? "ارزان‌تر از معمول" : "در محدوده‌ی معمول";
      $("wPeSub").textContent = `${word} — صدک ${faNum(v.percentile)} تاریخچه`;
    }
    if (v.median_pe != null) {
      $("wPeMore").innerHTML = `میانه‌ی تاریخی <b>${faNum(v.median_pe, 1)}</b>` +
        (v.earnings_yield_pct != null ? ` · بازده سود <b>${faNum(v.earnings_yield_pct, 1)}٪</b>` : "");
    }
    if (v.bill_yield_pct != null) {
      $("wBillValue").textContent = `${faNum(v.bill_yield_pct, 1)}٪`;
      $("wBillSub").textContent = "سود سالانه‌ی اوراق خزانه، معیار هر سرمایه‌گذاری";
      if (v.yield_gap_pct != null) {
        const gap = v.yield_gap_pct;
        $("wBillMore").innerHTML = `بازده سود بازار <b>${gap >= 0 ? "بالاتر" : "پایین‌تر"}</b> از اخزا، ` +
          `<b>${faNum(Math.abs(gap), 1)}</b> واحد`;
      }
    }
  }
}).catch(() => {
  $("wAsOf").textContent = "ارقام امروز خوانده نشد؛ پنل را باز کنید.";
});
