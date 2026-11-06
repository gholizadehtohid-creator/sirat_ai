/* Minimal SVG chart primitives for the SIRAT panel.
 *
 * Deliberately dependency-free: no CDN, so the panel renders on a machine with
 * no outbound internet (which is exactly the situation TSETMC access is in).
 *
 * Mark specs: 2px lines, 4px rounded data-ends anchored to the baseline, a 2px
 * surface gap between stacked fills, >=8px markers, solid hairline grid.
 */

const NS = "http://www.w3.org/2000/svg";

function tok(name) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim();
}

// Read a design token from a caller outside this module. Panels need the real
// colour to build reference lines (a resistance line must be the same red the
// resistance badge is), and a CSS var() cannot be used in an SVG presentation
// attribute -- so the value has to be resolved here.
export { tok as cssVar };

// The palette defines exactly this many categorical slots. Asking for more
// used to return "" for every slot past the last one, and an SVG stroke of ""
// draws nothing: the chart looked fine, axes and all, with whole series
// silently invisible. Cycling repeats a hue, which is a legibility problem the
// reader can see -- far better than one they cannot.
const SERIES_SLOTS = 6;

export function seriesColors(n) {
  return Array.from({ length: n },
    (_, i) => tok(`--series-${(i % SERIES_SLOTS) + 1}`));
}

function isNum(v) {
  return v !== null && v !== undefined && !Number.isNaN(v);
}

function el(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  if (parent) parent.appendChild(node);
  return node;
}

function svgRoot(container, width, height) {
  container.innerHTML = "";
  const svg = el("svg", {
    viewBox: `0 0 ${width} ${height}`,
    width: "100%",
    height: height,
    role: "img",
    preserveAspectRatio: "xMidYMid meet"
  }, container);
  return svg;
}

/* ---------- shared tooltip ---------- */

let tipNode = null;

function tip() {
  if (!tipNode) {
    tipNode = document.createElement("div");
    tipNode.className = "tip hidden";
    document.body.appendChild(tipNode);
  }
  return tipNode;
}

function showTip(html, evt) {
  const t = tip();
  t.innerHTML = html;
  t.classList.remove("hidden");
  const pad = 14;
  const r = t.getBoundingClientRect();
  let x = evt.clientX + pad;
  let y = evt.clientY + pad;
  if (x + r.width > window.innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = evt.clientY - r.height - pad;
  t.style.left = `${Math.max(8, x)}px`;
  t.style.top = `${Math.max(8, y)}px`;
}

export function hideTip() {
  if (tipNode) tipNode.classList.add("hidden");
}

function bindTip(node, html) {
  node.style.cursor = "default";
  node.addEventListener("mousemove", (e) => showTip(html, e));
  node.addEventListener("mouseleave", hideTip);
}

/* ---------- geometry helpers ---------- */

// Horizontal bar: rounded on the value end only, flat against the baseline.
function hBarPath(x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w, h / 2));
  if (w <= 0) return "";
  return `M${x},${y} H${x + w - r} A${r},${r} 0 0 1 ${x + w},${y + r} ` +
         `V${y + h - r} A${r},${r} 0 0 1 ${x + w - r},${y + h} H${x} Z`;
}

// Vertical bar growing up from the baseline.
function vBarPathUp(x, yTop, w, h, r) {
  r = Math.max(0, Math.min(r, h, w / 2));
  if (h <= 0) return "";
  return `M${x},${yTop + h} V${yTop + r} A${r},${r} 0 0 1 ${x + r},${yTop} ` +
         `H${x + w - r} A${r},${r} 0 0 1 ${x + w},${yTop + r} V${yTop + h} Z`;
}

// Vertical bar growing down from the baseline.
function vBarPathDown(x, yTop, w, h, r) {
  r = Math.max(0, Math.min(r, h, w / 2));
  if (h <= 0) return "";
  return `M${x},${yTop} V${yTop + h - r} A${r},${r} 0 0 1 ${x + r},${yTop + h} ` +
         `H${x + w - r} A${r},${r} 0 0 1 ${x + w},${yTop + h - r} V${yTop} Z`;
}

function niceTicks(min, max, count = 5) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 7.5 ? 10 : norm >= 3.5 ? 5 : norm >= 1.5 ? 2 : 1) * mag;
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 1e-6; v += step) {
    ticks.push(Math.abs(v) < step * 1e-6 ? 0 : +v.toFixed(10));
  }
  return ticks;
}

function fmt(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return v.toFixed(digits);
}

function axisText(parent, x, y, text, extra = {}) {
  const t = el("text", {
    x, y,
    fill: tok("--text-muted"),
    "font-size": 11,
    "font-family": tok("--font") || "system-ui, sans-serif",
    ...extra
  }, parent);
  t.textContent = text;
  return t;
}

/* ============================================================
 * 1. Horizontal bar chart — magnitude, one hue for every bar.
 *    (A value-ramp across nominal categories would double-encode
 *    length as hue, so every bar takes slot 1.)
 * ============================================================ */

export function hBarChart(container, opts) {
  const {
    data, labelKey = "label", valueKey = "value",
    max = null, format = (v) => fmt(v, 0),
    color = tok("--series-1"), tooltip = null
  } = opts;

  if (!data.length) { container.innerHTML = '<div class="empty">No data</div>'; return; }

  const rowH = 30, gap = 8;
  const padL = 78, padR = 56, padT = 6, padB = 24;
  const width = Math.max(container.clientWidth || 520, 320);
  const plotW = width - padL - padR;
  const height = padT + data.length * rowH + padB;

  const svg = svgRoot(container, width, height);
  const top = Math.max(max ?? Math.max(...data.map(d => d[valueKey])), 1);

  // hairline grid
  for (const t of niceTicks(0, top, 4)) {
    const x = padL + (t / top) * plotW;
    el("line", {
      x1: x, x2: x, y1: padT, y2: padT + data.length * rowH,
      stroke: tok("--grid"), "stroke-width": 1
    }, svg);
    axisText(svg, x, height - 8, format(t), { "text-anchor": "middle" });
  }

  data.forEach((d, i) => {
    const v = d[valueKey] || 0;
    const y = padT + i * rowH;
    const barH = rowH - gap;
    const w = Math.max(0, (v / top) * plotW);

    const path = el("path", {
      d: hBarPath(padL, y, w, barH, 4),
      fill: color
    }, svg);

    // Generous hit area: the bar plus its gap and the label gutter.
    const hit = el("rect", {
      x: 0, y, width, height: rowH, fill: "transparent"
    }, svg);

    axisText(svg, padL - 10, y + barH / 2 + 4, d[labelKey], {
      "text-anchor": "end", fill: tok("--text-secondary"),
      "font-size": 12, "font-weight": 550
    });

    // Direct label outside the bar end — never clipped inside a short bar.
    axisText(svg, padL + w + 8, y + barH / 2 + 4, format(v), {
      fill: tok("--text-primary"), "font-size": 12, "font-weight": 600
    });

    if (tooltip) bindTip(hit, tooltip(d));
    if (tooltip) bindTip(path, tooltip(d));
  });

  return svg;
}

/* ============================================================
 * 2. Stacked horizontal bar — part-to-whole, categorical series.
 *    2px surface gap between segments (no borders).
 * ============================================================ */

export function stackedHBar(container, opts) {
  const {
    rows, keys, colors, labelKey = "label",
    total = 100, format = (v) => fmt(v, 0), tooltip = null
  } = opts;

  if (!rows.length) { container.innerHTML = '<div class="empty">No data</div>'; return; }

  const rowH = 34, gap = 12, SEG_GAP = 2;
  const padL = 78, padR = 44, padT = 6, padB = 24;
  const width = Math.max(container.clientWidth || 520, 320);
  const plotW = width - padL - padR;
  const height = padT + rows.length * rowH + padB;

  const svg = svgRoot(container, width, height);

  for (const t of niceTicks(0, total, 4)) {
    const x = padL + (t / total) * plotW;
    el("line", {
      x1: x, x2: x, y1: padT, y2: padT + rows.length * rowH,
      stroke: tok("--grid"), "stroke-width": 1
    }, svg);
    axisText(svg, x, height - 8, format(t), { "text-anchor": "middle" });
  }

  rows.forEach((row, i) => {
    const y = padT + i * rowH;
    const barH = rowH - gap;
    let cursor = padL;
    let sum = 0;

    keys.forEach((key, k) => {
      const v = row.values[key] || 0;
      if (v <= 0) return;
      sum += v;

      const rawW = (v / total) * plotW;
      const w = Math.max(0, rawW - SEG_GAP);
      const isLast = keys.slice(k + 1).every(kk => !(row.values[kk] > 0));

      const seg = el("path", {
        d: hBarPath(cursor, y, w, barH, isLast ? 4 : 1.5),
        fill: colors[k]
      }, svg);

      bindTip(seg, tooltip
        ? tooltip(row, key, v)
        : `<div class="t-title">${row[labelKey]}</div>
           <div class="t-row"><span><span class="sw" style="background:${colors[k]}"></span>${key}</span><b>${format(v)}</b></div>`);

      cursor += rawW;
    });

    axisText(svg, padL - 10, y + barH / 2 + 4, row[labelKey], {
      "text-anchor": "end", fill: tok("--text-secondary"),
      "font-size": 12, "font-weight": 550
    });

    axisText(svg, padL + (sum / total) * plotW + 8, y + barH / 2 + 4, format(sum), {
      fill: tok("--text-primary"), "font-size": 12, "font-weight": 600
    });
  });

  return svg;
}

/* ============================================================
 * 3. Multi-line chart with crosshair + shared tooltip.
 *
 * Options beyond the obvious:
 *
 *   series[].color / .dash / .width
 *       A series that brings its own colour is a REFERENCE line, not data --
 *       an RSI 70 band, a support level, a zero baseline. It is drawn thin and
 *       dashed if asked, and it does not consume a palette slot, so adding
 *       four level lines to a price chart cannot push the price itself onto a
 *       hue that does not exist. A series with a falsy label gets no end
 *       label; three unlabelled reference lines stacked at the right edge is
 *       noise.
 *   yMin / yMax
 *       Pin the value axis. A bounded oscillator has to be read against its
 *       own bounds: auto-scaling RSI makes a 45-55 drift look like a crisis.
 *   bars
 *       {key, label, signKey} -- a column series measured FROM ZERO on the
 *       same axis, for a MACD histogram or a volume panel. Zero is forced into
 *       the domain, because a column chart whose baseline is not zero draws
 *       bar lengths that mean nothing.
 * ============================================================ */

export function lineChart(container, opts) {
  const {
    data, xKey = "date", series = [], height: h = 260,
    format = (v) => fmt(v, 1), directLabel = true,
    yMin = null, yMax = null, bars = null, zeroLine = false
  } = opts;

  const barKey = bars && bars.key ? bars.key : null;

  const points = data.filter(d =>
    series.some(s => isNum(d[s.key])) || (barKey && isNum(d[barKey])));

  if (!points.length) { container.innerHTML = '<div class="empty">No data</div>'; return; }

  const padL = 54, padR = directLabel ? 62 : 18, padT = 12, padB = 30;
  const width = Math.max(container.clientWidth || 560, 320);
  const plotW = width - padL - padR;
  const plotH = h - padT - padB;

  const svg = svgRoot(container, width, h);

  // Palette slots go only to series that did NOT bring their own colour, so a
  // reference line never displaces a data hue and the data series keep the
  // order the reader learned on the last chart.
  const auto = seriesColors(series.filter(s => !s.color).length);
  let slot = 0;
  const lines = series.map(s => ({ ...s, color: s.color || auto[slot++] }));

  const values = [];
  for (const d of points) {
    for (const s of lines) if (isNum(d[s.key])) values.push(d[s.key]);
    if (barKey && isNum(d[barKey])) values.push(d[barKey]);
  }

  if (!values.length) { container.innerHTML = '<div class="empty">No data</div>'; return; }

  let lo = Math.min(...values), hi = Math.max(...values);

  if (barKey) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }

  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.08 || 1;
  lo -= pad; hi += pad;

  // Padding below an all-positive column series would float the bars off their
  // own baseline. Clamp back onto zero (and the mirror case above it).
  if (barKey && Math.min(...values) >= 0) lo = 0;
  if (barKey && Math.max(...values) <= 0) hi = 0;

  if (yMin !== null && yMin !== undefined) lo = yMin;
  if (yMax !== null && yMax !== undefined) hi = yMax;
  if (hi <= lo) hi = lo + 1;

  const X = (i) => padL + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
  const Y = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;

  for (const t of niceTicks(lo, hi, 5)) {
    const y = Y(t);
    if (y < padT - 1 || y > padT + plotH + 1) continue;
    el("line", {
      x1: padL, x2: padL + plotW, y1: y, y2: y,
      stroke: tok("--grid"), "stroke-width": 1
    }, svg);
    axisText(svg, padL - 8, y + 4, format(t), { "text-anchor": "end" });
  }

  // x labels: a handful, evenly spaced, so they never collide
  const every = Math.max(1, Math.ceil(points.length / 6));
  points.forEach((d, i) => {
    if (i % every && i !== points.length - 1) return;
    axisText(svg, X(i), h - 10, String(d[xKey]).slice(2), { "text-anchor": "middle" });
  });

  el("line", {
    x1: padL, x2: padL + plotW, y1: padT + plotH, y2: padT + plotH,
    stroke: tok("--axis"), "stroke-width": 1
  }, svg);

  // ---- columns, under the lines so the lines stay readable over them ----
  if (barKey) {
    const slotW = plotW / Math.max(1, points.length);
    const barW = Math.max(1, Math.min(14, slotW * 0.68));
    const zeroY = Y(0);
    const pos = bars.posColor || tok("--diverge-pos");
    const neg = bars.negColor || tok("--diverge-neg");

    points.forEach((p, i) => {
      const v = p[barKey];
      if (!isNum(v)) return;
      // The sign that COLOURS the bar is not always the bar's own value: a
      // volume column is always positive, and what the reader wants to know is
      // whether that volume traded up or down.
      const sign = isNum(p[bars.signKey]) ? p[bars.signKey] : v;
      const y = Y(v);
      el("rect", {
        x: X(i) - barW / 2, y: Math.min(y, zeroY), width: barW,
        height: Math.max(0.75, Math.abs(y - zeroY)),
        fill: sign >= 0 ? pos : neg, opacity: 0.5, rx: 1
      }, svg);
    });
  }

  if ((zeroLine || barKey) && lo < 0 && hi > 0) {
    el("line", {
      x1: padL, x2: padL + plotW, y1: Y(0), y2: Y(0),
      stroke: tok("--axis"), "stroke-width": 1
    }, svg);
  }

  const endLabels = [];

  lines.forEach((s) => {
    const color = s.color;
    let d = "";
    let started = false;
    points.forEach((p, i) => {
      const v = p[s.key];
      if (!isNum(v)) { started = false; return; }
      d += `${started ? "L" : "M"}${X(i).toFixed(2)},${Y(v).toFixed(2)}`;
      started = true;
    });
    if (!d) return;

    el("path", {
      d, fill: "none", stroke: color, "stroke-width": s.width ?? 2,
      "stroke-dasharray": s.dash || null,
      "stroke-linejoin": "round", "stroke-linecap": "round"
    }, svg);

    if (directLabel && s.label) {
      for (let i = points.length - 1; i >= 0; i--) {
        const v = points[i][s.key];
        if (!isNum(v)) continue;
        endLabels.push({ x: X(i) + 7, y: Y(v), label: s.label, color });
        break;
      }
    }
  });

  // Series whose endpoints sit close together would otherwise stack their
  // labels on top of each other. Push them apart, keeping the original order.
  const LINE_H = 13;
  endLabels.sort((a, b) => a.y - b.y);
  for (let i = 1; i < endLabels.length; i++) {
    const gap = endLabels[i].y - endLabels[i - 1].y;
    if (gap < LINE_H) endLabels[i].y = endLabels[i - 1].y + LINE_H;
  }
  // Keep the whole stack inside the plot.
  const overflow = endLabels.length
    ? endLabels[endLabels.length - 1].y - (padT + plotH)
    : 0;
  if (overflow > 0) endLabels.forEach(l => { l.y -= overflow; });

  endLabels.forEach(l => {
    axisText(svg, l.x, l.y + 4, l.label, {
      fill: l.color, "font-size": 11, "font-weight": 600
    });
  });

  // crosshair layer
  const cross = el("line", {
    y1: padT, y2: padT + plotH, stroke: tok("--axis"),
    "stroke-width": 1, opacity: 0
  }, svg);

  // The dots and the tooltip swatches read the RESOLVED colour. They used to
  // read series[].color, which is undefined for every palette-coloured series
  // -- so el() dropped the attribute and every crosshair dot on the dashboard
  // rendered in SVG's default black, while the tooltip swatch got
  // "background:undefined" and showed nothing at all.
  // Only data series get a crosshair dot and a tooltip row. A dot riding a
  // constant reference line, and a tooltip row repeating "70" at every bar,
  // tell the reader nothing they did not already know -- and they crowd out
  // the rows that do. `ref: true` marks a series as a ruler rather than a
  // reading; it still gets its end label, which is how the reader knows what
  // the line is.
  const dots = lines.map(s => (s.label && !s.ref
    ? el("circle", {
        r: 4.5, fill: s.color, stroke: tok("--surface-1"),
        "stroke-width": 2, opacity: 0
      }, svg)
    : null));

  const overlay = el("rect", {
    x: padL, y: padT, width: plotW, height: plotH, fill: "transparent"
  }, svg);

  overlay.addEventListener("mousemove", (evt) => {
    const box = svg.getBoundingClientRect();
    const scale = width / box.width;
    const mx = (evt.clientX - box.left) * scale;
    const idx = Math.max(0, Math.min(points.length - 1,
      Math.round(((mx - padL) / plotW) * (points.length - 1))));
    const p = points[idx];

    cross.setAttribute("x1", X(idx));
    cross.setAttribute("x2", X(idx));
    cross.setAttribute("opacity", 1);

    let html = `<div class="t-title">${p[xKey]}</div>`;

    if (barKey && isNum(p[barKey])) {
      const sign = isNum(p[bars.signKey]) ? p[bars.signKey] : p[barKey];
      const swatch = sign >= 0
        ? (bars.posColor || tok("--diverge-pos"))
        : (bars.negColor || tok("--diverge-neg"));
      html += `<div class="t-row"><span><span class="sw" style="background:${swatch}"></span>${bars.label || barKey}</span><b>${(bars.format || format)(p[barKey])}</b></div>`;
    }

    lines.forEach((s, k) => {
      if (!dots[k]) return;                 // a reference line, not a reading
      const v = p[s.key];
      if (!isNum(v)) {
        dots[k].setAttribute("opacity", 0);
        return;
      }
      dots[k].setAttribute("cx", X(idx));
      dots[k].setAttribute("cy", Y(v));
      dots[k].setAttribute("opacity", 1);
      html += `<div class="t-row"><span><span class="sw" style="background:${s.color}"></span>${s.label}</span><b>${format(v)}</b></div>`;
    });
    showTip(html, evt);
  });

  overlay.addEventListener("mouseleave", () => {
    cross.setAttribute("opacity", 0);
    dots.forEach(d => d && d.setAttribute("opacity", 0));
    hideTip();
  });

  return svg;
}

/* ============================================================
 * 4. Diverging column chart — polarity around a zero baseline.
 * ============================================================ */

export function divergingBar(container, opts) {
  const {
    data, labelKey = "label", valueKey = "value",
    height: h = 240, format = (v) => fmt(v, 2), tooltip = null,
    // Sixty sessions of dates cannot all fit under their bars; every Nth,
    // plus the last, can. 1 (the default) labels every bar, as before.
    labelEvery = 1, padLeft = 52
  } = opts;

  if (!data.length) { container.innerHTML = '<div class="empty">No data</div>'; return; }

  const padL = padLeft, padR = 14, padT = 14, padB = 34;
  const width = Math.max(container.clientWidth || 520, 320);
  const plotW = width - padL - padR;
  const plotH = h - padT - padB;

  const svg = svgRoot(container, width, h);

  const vals = data.map(d => d[valueKey] || 0);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const pad = (hi - lo) * 0.15 || 1;
  lo -= pad; hi += pad;

  const Y = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;
  const zeroY = Y(0);

  for (const t of niceTicks(lo, hi, 5)) {
    const y = Y(t);
    el("line", {
      x1: padL, x2: padL + plotW, y1: y, y2: y,
      stroke: t === 0 ? tok("--axis") : tok("--grid"), "stroke-width": 1
    }, svg);
    axisText(svg, padL - 8, y + 4, format(t), { "text-anchor": "end" });
  }

  const slot = plotW / data.length;
  const barW = Math.min(46, slot * 0.62);

  const pos = tok("--diverge-pos");
  const neg = tok("--diverge-neg");

  data.forEach((d, i) => {
    const v = d[valueKey] || 0;
    const x = padL + i * slot + (slot - barW) / 2;
    const y = Y(v);
    const isPos = v >= 0;
    const barH = Math.abs(y - zeroY);

    const path = el("path", {
      d: isPos
        ? vBarPathUp(x, zeroY - barH, barW, barH, 4)
        : vBarPathDown(x, zeroY, barW, barH, 4),
      fill: isPos ? pos : neg
    }, svg);

    const hit = el("rect", {
      x: padL + i * slot, y: padT, width: slot, height: plotH, fill: "transparent"
    }, svg);

    if (labelEvery <= 1 || i % labelEvery === 0 ||
        (i === data.length - 1 && i % labelEvery >= labelEvery / 2)) {
      axisText(svg, padL + i * slot + slot / 2, h - 12, d[labelKey], {
        "text-anchor": "middle", "font-size": 11.5,
        fill: tok("--text-secondary"), "font-weight": 550
      });
    }

    const html = tooltip ? tooltip(d)
      : `<div class="t-title">${d[labelKey]}</div>
         <div class="t-row"><span>Value</span><b>${format(v)}</b></div>`;
    bindTip(path, html);
    bindTip(hit, html);
  });

  return svg;
}

/* ============================================================
 * 5. Equity curve — single series, so no legend box; the title names it.
 * ============================================================ */

export function areaLine(container, opts) {
  const {
    data, xKey = "date", yKey = "equity", height: h = 240,
    baseline = 100, format = (v) => fmt(v, 1), tooltip = null
  } = opts;

  if (!data.length) { container.innerHTML = '<div class="empty">No trades to plot</div>'; return; }

  const padL = 54, padR = 18, padT = 14, padB = 30;
  const width = Math.max(container.clientWidth || 520, 320);
  const plotW = width - padL - padR;
  const plotH = h - padT - padB;

  const svg = svgRoot(container, width, h);

  const vals = data.map(d => d[yKey]);
  let lo = Math.min(baseline, ...vals), hi = Math.max(baseline, ...vals);
  const pad = (hi - lo) * 0.12 || 1;
  lo -= pad; hi += pad;

  const X = (i) => padL + (data.length === 1 ? plotW / 2 : (i / (data.length - 1)) * plotW);
  const Y = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;

  for (const t of niceTicks(lo, hi, 5)) {
    const y = Y(t);
    if (y < padT - 1 || y > padT + plotH + 1) continue;
    el("line", {
      x1: padL, x2: padL + plotW, y1: y, y2: y,
      stroke: tok("--grid"), "stroke-width": 1
    }, svg);
    axisText(svg, padL - 8, y + 4, format(t), { "text-anchor": "end" });
  }

  // break-even reference
  const beY = Y(baseline);
  el("line", {
    x1: padL, x2: padL + plotW, y1: beY, y2: beY,
    stroke: tok("--axis"), "stroke-width": 1
  }, svg);
  axisText(svg, padL + plotW, beY - 6, "break-even", {
    "text-anchor": "end", "font-size": 10.5
  });

  const last = data[data.length - 1][yKey];
  const color = last >= baseline ? tok("--series-1") : tok("--diverge-neg");

  let line = "";
  data.forEach((d, i) => {
    line += `${i ? "L" : "M"}${X(i).toFixed(2)},${Y(d[yKey]).toFixed(2)}`;
  });

  const fillId = `grad-${Math.random().toString(36).slice(2, 8)}`;
  const defs = el("defs", {}, svg);
  const grad = el("linearGradient", {
    id: fillId, x1: 0, y1: 0, x2: 0, y2: 1
  }, defs);
  el("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.22 }, grad);
  el("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0.02 }, grad);

  el("path", {
    d: `${line}L${X(data.length - 1)},${padT + plotH}L${padL},${padT + plotH}Z`,
    fill: `url(#${fillId})`, stroke: "none"
  }, svg);

  el("path", {
    d: line, fill: "none", stroke: color, "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round"
  }, svg);

  const every = Math.max(1, Math.ceil(data.length / 6));
  data.forEach((d, i) => {
    if (i % every && i !== data.length - 1) return;
    axisText(svg, X(i), h - 10, String(d[xKey]).slice(2), { "text-anchor": "middle" });
  });

  const dot = el("circle", {
    r: 4.5, fill: color, stroke: tok("--surface-1"),
    "stroke-width": 2, opacity: 0
  }, svg);

  const overlay = el("rect", {
    x: padL, y: padT, width: plotW, height: plotH, fill: "transparent"
  }, svg);

  overlay.addEventListener("mousemove", (evt) => {
    const box = svg.getBoundingClientRect();
    const scale = width / box.width;
    const mx = (evt.clientX - box.left) * scale;
    const idx = Math.max(0, Math.min(data.length - 1,
      Math.round(((mx - padL) / plotW) * (data.length - 1))));
    const d = data[idx];
    dot.setAttribute("cx", X(idx));
    dot.setAttribute("cy", Y(d[yKey]));
    dot.setAttribute("opacity", 1);
    showTip(tooltip ? tooltip(d)
      : `<div class="t-title">${d[xKey]}</div>
         <div class="t-row"><span>Equity</span><b>${format(d[yKey])}</b></div>`, evt);
  });

  overlay.addEventListener("mouseleave", () => {
    dot.setAttribute("opacity", 0);
    hideTip();
  });

  return svg;
}

export { fmt };
