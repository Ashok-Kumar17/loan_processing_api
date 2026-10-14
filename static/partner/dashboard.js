// Partner dashboard: plain JS against the JWT-protected /api/partner/* endpoints.
const $ = (sel) => document.querySelector(sel);
const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2 });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let token = null;
try { token = sessionStorage.getItem("lamf_partner_token"); } catch (e) { /* storage unavailable */ }

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.headers || {}) },
  });
  if (res.status === 401 || res.status === 403) { logout(); throw new Error("Session expired - sign in again."); }
  const body = res.status === 204 ? null : await res.json();
  if (!res.ok) throw new Error(body?.detail || JSON.stringify(body));
  return body;
}

function logout() {
  token = null;
  try { sessionStorage.removeItem("lamf_partner_token"); } catch (e) {}
  $("#app").hidden = true; $("#login").hidden = false; $("#who").innerHTML = "";
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("#login-error").textContent = "";
  const form = new FormData(ev.target);
  try {
    const res = await fetch("/api/auth/token/", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Login failed");
    token = body.access;
    try { sessionStorage.setItem("lamf_partner_token", token); } catch (e) {}
    await load();
  } catch (err) { $("#login-error").textContent = err.message; }
});

$("#link-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const label = new FormData(ev.target).get("label");
  await api("/api/partner/referral-links/", { method: "POST", body: JSON.stringify({ label }) });
  ev.target.reset();
  await load();
});

async function copyLink(url, btn) {
  try { await navigator.clipboard.writeText(url); btn.textContent = "Copied"; }
  catch (e) { window.prompt("Copy this link", url); }
  setTimeout(() => (btn.textContent = "Copy link"), 1500);
}

async function load() {
  const [dash, links, clients, months] = await Promise.all([
    api("/api/partner/dashboard/"),
    api("/api/partner/referral-links/"),
    api("/api/partner/clients/"),
    api("/api/partner/commissions/"),
  ]);
  $("#login").hidden = true; $("#app").hidden = false;
  $("#who").innerHTML = `${esc(dash.partner)} <button class="ghost" id="logout">Sign out</button>`;
  $("#logout").onclick = logout;

  const kpis = [
    ["Referred clients", dash.clients],
    ["Active credit lines", dash.active_lines],
    ["Sanctioned (active)", inr.format(dash.sanctioned_total)],
    ["Outstanding", inr.format(dash.outstanding_total)],
    ["Commission this month", inr2.format(dash.commission_this_month)],
    ["Commission lifetime", inr2.format(dash.commission_lifetime)],
  ];
  $("#kpis").innerHTML = kpis.map(([l, v]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${esc(v)}</div></div>`).join("");

  $("#links").innerHTML = links.results.map((l) => `
    <tr><td>${esc(l.label)}</td><td class="code">${esc(l.code)}</td>
    <td class="num">${l.signups}</td><td class="num">${l.active_lines}</td>
    <td class="num"><button class="ghost" data-url="${esc(l.share_url)}">Copy link</button></td></tr>`).join("");
  document.querySelectorAll("#links button").forEach((b) => (b.onclick = () => copyLink(b.dataset.url, b)));

  $("#months").innerHTML = months.map((m) => `
    <tr><td>${new Date(m.month).toLocaleDateString("en-IN", { month: "short", year: "numeric" })}</td>
    <td class="num">${m.lines}</td><td class="num">${inr2.format(m.amount)}</td></tr>`).join("")
    || `<tr><td colspan="3" class="muted">No commission accrued yet.</td></tr>`;

  $("#clients").innerHTML = clients.results.map((c) => `
    <tr><td>${esc(c.username)}</td><td>${new Date(c.joined).toLocaleDateString("en-IN")}</td>
    <td class="code">${esc(c.referral_code)}</td>
    <td>${c.line_status ? `<span class="chip">${esc(c.line_status)}</span>` : `<span class="chip none">none</span>`}</td>
    <td class="num">${inr.format(c.sanctioned_limit)}</td><td class="num">${inr.format(c.outstanding)}</td>
    <td class="num">${inr2.format(c.commission_earned)}</td></tr>`).join("");
}

if (token) load().catch(logout);
