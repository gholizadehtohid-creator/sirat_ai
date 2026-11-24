/* The Bale bot's admin page (2026-11-24). Reads /api/bot/state and writes
   through /api/bot/config, /api/bot/users/<id> and /api/bot/broadcast. The
   token is sent once and never shown again: the server returns only whether
   it is set and its last four characters. */

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const STATUS_FA = { pending: "در انتظار تأیید", approved: "تأییدشده", blocked: "مسدود" };
const STATUS_CLS = { pending: "warn", approved: "good", blocked: "bad" };
const ROLE_FA = { user: "کاربر", admin: "ادمین" };

let state = null;

function banner(kind, html) {
  const node = $("botBanner");
  node.classList.remove("is-error", "is-live", "is-idle");
  if (kind) node.classList.add(kind);
  node.querySelector(".icon").textContent =
    kind === "is-live" ? "✓" : kind === "is-error" ? "✕" : kind === "is-idle" ? "…" : "⚠";
  $("botBannerText").innerHTML = html;
}

async function call(url, body) {
  const res = await fetch(url, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (err) { /* not JSON */ }
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return esc(iso);
  return d.toLocaleString("fa-IR", { dateStyle: "short", timeStyle: "short" });
}

function tile(k, v, sub) {
  return `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${v}</div>` +
    (sub ? `<div class="sub">${sub}</div>` : "") + `</div>`;
}

function renderState(s) {
  state = s;
  const c = s.config || {};
  const r = s.runner || {};
  const u = (s.stats || {}).users || {};
  $("botKpis").innerHTML = [
    tile("ربات", r.running ? '<span class="badge good"><span class="dot"></span>در حال اجرا</span>'
                           : '<span class="badge neutral"><span class="dot"></span>خاموش</span>',
         r.username ? "@" + esc(r.username) : (c.token_set ? "توکن ثبت شده " + esc(c.token_hint) : "توکنی ثبت نشده")),
    tile("کاربران تأییدشده", esc(u.approved || 0), "در انتظار: " + esc(u.pending || 0) + " · مسدود: " + esc(u.blocked || 0)),
    tile("آخرین دریافت پیام", r.last_poll ? when(r.last_poll) : "—", r.started_at ? "از " + when(r.started_at) : ""),
    tile("صف ارسال", esc((s.stats || {}).outbox_waiting || 0), "پیام در انتظار ارسال"),
  ].join("");

  $("summaryTime").value = c.summary_time || "19:30";
  $("alertsTime").value = c.alerts_time || "20:00";
  $("dailyQuota").value = c.daily_quota ?? 60;
  $("pdfQuota").value = c.pdf_quota ?? 5;
  $("enabledInput").checked = !!c.enabled;

  if (r.last_error) {
    banner("is-error", "آخرین خطای ربات: " + esc(r.last_error));
  } else if (!c.token_set) {
    banner("", "هنوز توکنی ثبت نشده است. راهنمای سه‌قدمی بالا را ببینید.");
  } else if (c.enabled && !r.running) {
    banner("", "ربات روشن است ولی اجرا نمی‌شود. پنل را دوباره اجرا کنید یا «ذخیره» را بزنید.");
  } else {
    banner("is-live", r.running ? "ربات در حال اجراست." : "ربات خاموش است.");
  }
  renderUsers();
  renderStats();
}

function renderUsers() {
  const filter = $("statusFilter").value;
  const users = (state.users || []).filter((u) => !filter || u.status === filter);
  if (!users.length) {
    $("usersTable").innerHTML = '<p class="hint" style="padding:12px">کاربری نیست.</p>';
    return;
  }
  const head = ["نام", "نام کاربری", "شناسه", "وضعیت", "نقش", "عضویت", "آخرین فعالیت",
                "امروز", "سبد", "سهمیه", "اقدام"];
  const rows = users.map((u) => {
    const name = [u.first_name, u.last_name].filter(Boolean).join(" ") || "—";
    const today = u.today || {};
    const quota = `${u.daily_quota ?? "پیش‌فرض"} / PDF ${u.pdf_quota ?? "پیش‌فرض"}`;
    const acts = [];
    if (u.status !== "approved") acts.push(["approve", "تأیید"]);
    if (u.status !== "blocked") acts.push(["block", "مسدود"]);
    if (u.status === "approved") acts.push(u.role === "admin" ? ["user", "برداشتن ادمین"] : ["admin", "ادمین کن"]);
    acts.push(["quota", "سهمیه"], ["delete", "حذف"]);
    return `<tr><td>${esc(name)}${u.note ? `<div class="hint">${esc(u.note)}</div>` : ""}</td>` +
      `<td>${u.username ? "@" + esc(u.username) : "—"}</td><td>${esc(u.chat_id)}</td>` +
      `<td><span class="badge ${STATUS_CLS[u.status] || "neutral"}"><span class="dot"></span>${esc(STATUS_FA[u.status] || u.status)}</span></td>` +
      `<td>${esc(ROLE_FA[u.role] || u.role)}</td><td>${when(u.created_at)}</td><td>${when(u.last_seen)}</td>` +
      `<td>${esc(today.request || 0)} درخواست · ${esc(today.pdf || 0)} PDF</td>` +
      `<td>${esc(u.holdings || 0)} نماد</td><td>${esc(quota)}</td>` +
      `<td class="bot-acts">${acts.map(([a, t]) =>
        `<button type="button" data-act="${a}" data-id="${esc(u.chat_id)}">${esc(t)}</button>`).join("")}</td></tr>`;
  });
  $("usersTable").innerHTML = `<table><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}` +
    `</tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

function renderStats() {
  const s = state.stats || {};
  const kinds = s.last_24h || [];
  $("statsTable").innerHTML = kinds.length
    ? `<table><thead><tr><th>نوع</th><th>تعداد</th><th>ناموفق</th></tr></thead><tbody>` +
      kinds.map((k) => `<tr><td>${esc(k.kind)}</td><td>${esc(k.n)}</td><td>${esc(k.failed || 0)}</td></tr>`).join("") +
      `</tbody></table>`
    : '<p class="hint" style="padding:12px">در ۲۴ ساعت گذشته درخواستی نبوده است.</p>';
  $("errorsList").innerHTML = (s.recent_errors || []).map((e) =>
    `<li class="warn"><span class="what">${when(e.at)} — ${esc(e.kind)} (${esc(e.chat_id ?? "—")})</span>` +
    `<span class="todo">${esc(e.detail || "")}</span></li>`).join("");
}

async function load() {
  try {
    renderState(await call("/api/bot/state"));
  } catch (err) {
    banner("is-error", "وضعیت ربات خوانده نشد: " + esc(err.message));
  }
}

$("configForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {
    summary_time: $("summaryTime").value, alerts_time: $("alertsTime").value,
    daily_quota: Number($("dailyQuota").value), pdf_quota: Number($("pdfQuota").value),
    enabled: $("enabledInput").checked,
  };
  const token = $("tokenInput").value.trim();
  if (token) body.token = token;
  banner("is-idle", "در حال ذخیره…");
  try {
    renderState(await call("/api/bot/config", body));
    $("tokenInput").value = "";
  } catch (err) {
    banner("is-error", "ذخیره نشد: " + esc(err.message));
  }
});

$("usersTable").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const act = btn.dataset.act;
  const id = btn.dataset.id;
  const body = { action: act };
  if (act === "delete" && !window.confirm("این کاربر و سبدش حذف شود؟")) return;
  if (act === "quota") {
    const daily = window.prompt("سهمیه‌ی درخواست روزانه (خالی = پیش‌فرض):", "");
    if (daily === null) return;
    const pdf = window.prompt("سهمیه‌ی PDF روزانه (خالی = پیش‌فرض):", "");
    if (pdf === null) return;
    body.daily_quota = daily.trim() === "" ? null : Number(daily);
    body.pdf_quota = pdf.trim() === "" ? null : Number(pdf);
  }
  btn.disabled = true;
  try {
    renderState(await call(`/api/bot/users/${encodeURIComponent(id)}`, body));
  } catch (err) {
    banner("is-error", "انجام نشد: " + esc(err.message));
    btn.disabled = false;
  }
});

$("broadcastForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("broadcastText").value.trim();
  if (!text) return;
  const audience = $("broadcastAudience").value;
  if (!window.confirm("این پیام برای " + (audience === "admins" ? "ادمین‌ها" : "همه‌ی کاربران تأییدشده") + " فرستاده شود؟")) return;
  try {
    const res = await call("/api/bot/broadcast", { text, audience });
    $("broadcastText").value = "";
    banner("is-live", `پیام برای ${esc(res.queued)} نفر در صف ارسال گذاشته شد.`);
    load();
  } catch (err) {
    banner("is-error", "ارسال نشد: " + esc(err.message));
  }
});

$("statusFilter").addEventListener("change", () => state && renderUsers());
$("reloadBtn").addEventListener("click", load);

load();
setInterval(load, 30000);
