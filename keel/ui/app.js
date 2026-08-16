/* KEEL operator console. No frameworks, no network dependencies — everything
   renders from the local API. */

const $ = (sel, root = document) => root.querySelector(sel);
// null-safe: the auth screen replaces <body>, so chrome elements can be absent
const setText = (sel, value) => { const el = $(sel); if (el) el.textContent = value; };
let DOM = localStorage.getItem("keel-domain") || "";
const withDomain = (path) =>
  path.startsWith("/api") || path === "/a2a"
    ? path + (path.includes("?") ? "&" : "?") + "domain=" + encodeURIComponent(DOM)
    : path;
const api = async (path, opts) => {
  const r = await fetch(withDomain(path), opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}) });

/* ── DOM helpers ─────────────────────────────────────────────────────────── */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return el;
}
const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}
const fmtT = (ts) => new Date(ts * 1000).toISOString().slice(11, 19) + "Z";
const fmtD = (ts) => new Date(ts * 1000).toISOString().slice(0, 10);
const ago = (ts) => {
  const d = Date.now() / 1000 - ts;
  if (d < 90) return `${d | 0}s ago`;
  if (d < 5400) return `${(d / 60) | 0}m ago`;
  if (d < 129600) return `${(d / 3600) | 0}h ago`;
  return `${(d / 86400) | 0}d ago`;
};
const pct = (x, d = 0) => x == null ? "—" : (100 * x).toFixed(d) + "%";
const toast = (msg, kind = "") => {
  const t = h("div", { class: `toast ${kind}` }, msg);
  $("#toasts").append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 5200);
  setTimeout(() => t.remove(), 5700);
};
const tip = $("#tip") || { style: {} };
/* escape any value that reaches tooltip markup — alarm feeds are untrusted */
const esc = (x) => String(x).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function showTip(ev, html) {
  tip.innerHTML = html; tip.style.display = "block";
  const x = Math.min(ev.clientX + 14, innerWidth - 300);
  tip.style.left = x + "px"; tip.style.top = (ev.clientY + 12) + "px";
}
const hideTip = () => { tip.style.display = "none"; };

/* ── charts (dataviz-spec marks: thin, rounded, recessive grid) ─────────── */
const SERIES = ["#2F49C9", "#B06E10", "#0B7A5A", "#A8438F"];

function sparkline(points, { w = 200, ht = 44, color = SERIES[0], fill = true, yfmt = (v) => v.toFixed(2) } = {}) {
  if (!points.length) return s("svg", { width: w, height: ht });
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const X = (x) => 2 + (w - 4) * (x1 === x0 ? 0.5 : (x - x0) / (x1 - x0));
  const Y = (y) => ht - 4 - (ht - 10) * (y1 === y0 ? 0.5 : (y - y0) / (y1 - y0));
  const d = points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  const svg = s("svg", { width: w, height: ht, viewBox: `0 0 ${w} ${ht}` });
  if (fill) svg.append(s("path", { d: d + `L${X(x1)},${ht - 2}L${X(x0)},${ht - 2}Z`, fill: color, opacity: 0.12 }));
  svg.append(s("path", { d, stroke: color, "stroke-width": 2, fill: "none", "stroke-linecap": "round" }));
  const last = points[points.length - 1];
  svg.append(s("circle", { cx: X(last[0]), cy: Y(last[1]), r: 3, fill: color, stroke: "#FFFFFF", "stroke-width": 1.5 }));
  svg.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const fx = x0 + ((ev.clientX - r.left) / r.width) * (x1 - x0 || 1);
    let best = points[0];
    for (const p of points) if (Math.abs(p[0] - fx) < Math.abs(best[0] - fx)) best = p;
    showTip(ev, esc(yfmt(best[1])));
  });
  svg.addEventListener("mouseleave", hideTip);
  return svg;
}

function arcGauge(value, max, { w = 120, label = "", color = "#2F49C9" } = {}) {
  const r = 46, cx = w / 2, cy = 58;
  const a0 = Math.PI * 1.16, a1 = -Math.PI * 0.16;
  const arc = (t0, t1, rad, col, sw, cap = "round") => {
    const p0 = [cx + rad * Math.cos(t0), cy - rad * Math.sin(t0)];
    const p1 = [cx + rad * Math.cos(t1), cy - rad * Math.sin(t1)];
    const large = Math.abs(t0 - t1) > Math.PI ? 1 : 0;
    return s("path", { d: `M${p0[0]},${p0[1]} A${rad},${rad} 0 ${large} 1 ${p1[0]},${p1[1]}`,
      stroke: col, "stroke-width": sw, fill: "none", "stroke-linecap": cap });
  };
  const svg = s("svg", { width: w, height: 74, viewBox: `0 0 ${w} 74` });
  svg.append(arc(a0, a1, r, "#E7EBE5", 7));
  const frac = Math.max(0.02, Math.min(1, value / max));
  svg.append(arc(a0, a0 + (a1 - a0) * frac, r, color, 7));
  svg.append(s("text", { x: cx, y: cy - 6, "text-anchor": "middle",
    style: "font:600 20px var(--mono);fill:var(--ink)" }, String(value)));
  if (label) svg.append(s("text", { x: cx, y: cy + 10, "text-anchor": "middle", class: "axis-lab" }, label));
  return svg;
}

function barsH(rows, { w = 380, barH = 22, gap = 10, max = null, fmt = (v) => v.toFixed(2), colors = null, labW = 130 } = {}) {
  const valW = 52;
  const m = max ?? Math.max(...rows.map((r) => r.value), 0.0001);
  const ht = rows.length * (barH + gap);
  const svg = s("svg", { width: "100%", height: ht, viewBox: `0 0 ${w} ${ht}`, preserveAspectRatio: "xMinYMin meet" });
  rows.forEach((row, i) => {
    const y = i * (barH + gap);
    const bw = Math.max(2, (w - labW - valW - 10) * (row.value / m));
    const col = colors ? colors[i % colors.length] : (row.color || SERIES[0]);
    svg.append(s("text", { x: labW - 8, y: y + barH / 2 + 3.5, "text-anchor": "end", class: "bar-lab" }, row.label));
    svg.append(s("rect", { x: labW, y: y + barH / 2 - 1, width: w - labW - valW - 10, height: 2, fill: "#E7EBE5", rx: 1 }));
    const bar = s("rect", { x: labW, y: y + 3, width: bw, height: barH - 6, rx: 4, fill: col });
    bar.addEventListener("mousemove", (ev) => showTip(ev, `${esc(row.label)} · <b>${esc(fmt(row.value))}</b>${esc(row.extra || "")}`));
    bar.addEventListener("mouseleave", hideTip);
    svg.append(bar);
    svg.append(s("text", { x: w - valW + 6, y: y + barH / 2 + 3.5, class: "bar-lab", style: "fill:var(--ink)" }, fmt(row.value)));
  });
  return svg;
}

function lineChart(seriesList, { w = 560, ht = 190, x0 = 0, x1 = 1, y0 = 0, y1 = 1,
  xfmt = (v) => v.toFixed(1), yfmt = (v) => v.toFixed(2), refY = null, refLabel = "" } = {}) {
  const padL = 44, padR = 14, padT = 12, padB = 24;
  const X = (x) => padL + (w - padL - padR) * ((x - x0) / (x1 - x0 || 1));
  const Y = (y) => ht - padB - (ht - padT - padB) * ((y - y0) / (y1 - y0 || 1));
  const svg = s("svg", { width: "100%", height: ht, viewBox: `0 0 ${w} ${ht}`, preserveAspectRatio: "xMidYMid meet" });
  for (let i = 0; i <= 4; i++) {
    const yv = y0 + ((y1 - y0) * i) / 4;
    svg.append(s("line", { x1: padL, x2: w - padR, y1: Y(yv), y2: Y(yv), class: "grid-line" }));
    svg.append(s("text", { x: padL - 7, y: Y(yv) + 3.5, "text-anchor": "end", class: "axis-lab" }, yfmt(yv)));
  }
  [x0, (x0 + x1) / 2, x1].forEach((xv) =>
    svg.append(s("text", { x: X(xv), y: ht - 7, "text-anchor": "middle", class: "axis-lab" }, xfmt(xv))));
  if (refY != null) {
    svg.append(s("line", { x1: padL, x2: w - padR, y1: Y(refY), y2: Y(refY),
      stroke: "#8B9A93", "stroke-width": 1.4, "stroke-dasharray": "5 5" }));
    svg.append(s("text", { x: w - padR - 2, y: Y(refY) - 5, "text-anchor": "end", class: "axis-lab" }, refLabel));
  }
  seriesList.forEach((ser) => {
    if (!ser.points.length) return;
    const d = ser.points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    svg.append(s("path", { d, stroke: ser.color, "stroke-width": 2, fill: "none", "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const last = ser.points[ser.points.length - 1];
    svg.append(s("circle", { cx: X(last[0]), cy: Y(last[1]), r: 3.5, fill: ser.color, stroke: "#FFFFFF", "stroke-width": 1.5 }));
    if (ser.label) svg.append(s("text", { x: X(last[0]) - 6, y: Y(last[1]) - 8, "text-anchor": "end",
      class: "bar-lab", style: `fill:${ser.color}` }, ser.label));
  });
  svg.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const fx = x0 + ((ev.clientX - r.left) / r.width) * (x1 - x0);
    const rows = seriesList.map((ser) => {
      let best = null;
      for (const p of ser.points) if (!best || Math.abs(p[0] - fx) < Math.abs(best[0] - fx)) best = p;
      return best ? `<span style="color:${ser.color}">●</span> ${esc(ser.label || "")} ${esc(yfmt(best[1]))}` : "";
    }).filter(Boolean);
    if (rows.length) showTip(ev, rows.join("<br>"));
  });
  svg.addEventListener("mouseleave", hideTip);
  return svg;
}

function pnBar(a) {
  const bounded = a.pn == null;
  const lo = a.pn_lo ?? 0, hi = a.pn_hi ?? 1;
  const wrap = h("div", { class: `pnbar ${bounded ? "bounds" : ""}` });
  wrap.append(h("div", { class: "track" }));
  const ci = h("div", { class: "ci", style: `left:${lo * 100}%;width:${(hi - lo) * 100}%` });
  wrap.append(ci);
  if (!bounded) wrap.append(h("div", { class: "pt", style: `left:calc(${a.pn * 100}% - 5px)` }));
  wrap.append(h("div", { class: "lab" },
    bounded ? `PN ∈ [${lo.toFixed(2)}, ${hi.toFixed(2)}] (bounds)` :
      `PN ${a.pn.toFixed(2)} [${lo.toFixed(2)}, ${hi.toFixed(2)}]`));
  return wrap;
}

/* ── layout chrome ───────────────────────────────────────────────────────── */
const VIEWS = [
  ["deck", "Deck", "1"], ["certs", "Certificates", "2"], ["atlas", "Causal Atlas", "3"],
  ["calibration", "Calibration", "4"], ["ledger", "Ledger", "5"],
  ["evidence", "Evidence", "6"], ["policy", "Policy", "7"],
  ["connect", "＋ Connect data", "8"],
  ["gateway", "Agent Gateway", "9"],
  ["billing", "Upgrade · Team", "0"],
];
let PACKS = [];
const isSandbox = () => (PACKS.find((p) => p.key === DOM) || { sandbox: true }).sandbox;
function renderNav(active) {
  const nav = $("#nav"); if (!nav) return; nav.innerHTML = "";
  for (const [key, label, k] of VIEWS) {
    nav.append(h("a", { href: `#/${key}`, class: active === key ? "active" : "" },
      h("span", { class: "k" }, k), label));
  }
}
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.metaKey || e.ctrlKey) return;
  const v = VIEWS.find(([, , k]) => k === e.key);
  if (v) location.hash = `#/${v[0]}`;
});
setInterval(() => setText("#clock", new Date().toISOString().slice(11, 19) + "Z"), 500);

async function initDomains() {
  const sel = $("#domain-sel");
  if (!sel) return;
  try {
    PACKS = await fetch("/api/domains").then((r) => r.json());
    if (DOM !== "gateway" && !PACKS.find((p) => p.key === DOM))
      DOM = PACKS.length ? PACKS[0].key : "";
    sel.innerHTML = "";
    if (!PACKS.length)
      sel.append(h("option", { value: "" }, "no workspaces yet"));
    const mine = PACKS.filter((p) => !p.sandbox);
    if (mine.length) {
      const g = h("optgroup", { label: "Your workspaces" });
      mine.forEach((p) => g.append(h("option", { value: p.key }, `◆ ${p.name}`)));
      sel.append(g);
    }
    const g2 = h("optgroup", { label: "Sandbox demos" });
    PACKS.filter((p) => p.sandbox).forEach((p) =>
      g2.append(h("option", { value: p.key }, `${p.icon} ${p.name}`)));
    sel.append(g2);
    sel.append(h("option", { value: "__connect" }, "＋ Connect your data…"));
    sel.value = DOM;
    sel.onchange = async () => {
      if (sel.value === "__connect") {
        sel.value = DOM;
        location.hash = "#/connect";
        return;
      }
      const packInfo = PACKS.find((p) => p.key === sel.value);
      DOM = sel.value;
      localStorage.setItem("keel-domain", DOM);
      if (packInfo && packInfo.sandbox && !packInfo.seeded)
        toast(`Preparing ${packInfo.name} — first boot seeds 90 days of history (~10 s)…`);
      location.hash = "#/deck";
      await refreshOverview();
      route();
    };
  } catch { /* server booting */ }
}
initDomains();

let OV = null;
async function refreshOverview() {
  if (!DOM || DOM === "gateway") {
    setText("#foot-tenant", DOM || "no workspace");
    return;
  }
  try {
    OV = await api("/api/overview");
    setText("#foot-tenant", OV.tenant);
    setText("#strip-graph", OV.graph_version.replace("G-", ""));
    setText("#strip-cal", `n=${OV.calibration_n}`);
    const cov = OV.coverage?.marginal;
    setText("#strip-cov", cov == null ? "—" : `${pct(cov)} / ${pct(1 - OV.alpha)}`);
    setText("#strip-root", OV.translog_root.slice(0, 14) + "…");
    const lamp = $("#foot-lamp");
    if (lamp) lamp.className = `lamp ${OV.drift.level}`;
    setText("#foot-drift", OV.drift.level === "ok" ? "drift nominal" :
      OV.drift.level === "widened" ? "drift — intervals widened" : "drift breach — abstaining");
    setText("#foot-tier", `T${OV.autonomy.tier}`);
    setText("#foot-corpus", `${OV.autonomy.successes}/${OV.autonomy.executed} actions ok`);
    if (ACCOUNT && ACCOUNT.email && ACCOUNT.email !== "default@local") {
      const ft = $("#foot-tenant");
      if (!ft) return;
      ft.textContent = ACCOUNT.email;
      ft.style.cursor = "pointer"; ft.title = "sign out";
      ft.onclick = async () => { await fetch("/api/auth/logout", { method: "POST" }); location.reload(); };
    }
  } catch { /* server still booting */ }
}
setInterval(refreshOverview, 15000);

/* ── DECK ────────────────────────────────────────────────────────────────── */
async function viewDeck(root) {
  const [ov, incidents, net, scenarios] = await Promise.all([
    api("/api/overview"), api("/api/incidents"), api("/api/network"), api("/api/scenarios")]);
  OV = ov;
  const open = incidents.filter((i) => ["open", "verifying"].includes(i.status));
  const hero = open[0] || incidents.find((i) => i.status === "certified") || incidents[0];

  const byo = scenarios.length === 0;      // BYO workspace: no simulator, ever
  const scenarioSel = h("select", { style: "background:var(--panel-3);color:var(--ink);border:1px solid var(--hairline);border-radius:6px;padding:8px 10px;font:12.5px var(--sans)" },
    scenarios.map((sc) => h("option", { value: sc.key }, sc.key.replaceAll("_", " "))));

  root.append(
    h("div", { class: "deck-hero" },
      h("div", { class: "eyebrow" }, hero && hero.status === "open" ? "Incident awaiting verification" : "Operations deck"),
      h("h2", {}, hero ? hero.title :
        (byo ? "Connected and listening — no incidents detected yet"
             : "All quiet on the network")),
      hero ? h("div", { class: "meta" },
        h("span", { class: "mono" }, hero.incident_id), " · ",
        `${hero.alarm_count} alarms · ${hero.sla_services.length} services impacted · ${ago(hero.t0)} · `,
        h("span", { class: `status-word ${hero.status}` }, hero.status.toUpperCase())) : null,
      h("div", { class: "actions" },
        hero ? h("button", { class: "primary", onclick: () => location.hash = `#/incident/${hero.incident_id}` },
          hero.status === "open" ? "Open war room →" : "View incident →") : null,
        h("span", { style: "flex:1" }),
        ...(byo
          ? [h("button", { onclick: () => location.hash = "#/connect" },
               "Manage data connection →")]
          : [scenarioSel,
             h("button", {
               onclick: async () => {
                 const res = await post("/api/incidents/simulate", { scenario: scenarioSel.value });
                 toast(`Simulated ${res.incident.incident_id}`, "good");
                 location.hash = `#/incident/${res.incident.incident_id}`;
               } }, "Inject incident")]))),
    h("div", { class: "grid instruments" },
      instrument(arcGauge(ov.autonomy.tier, 3, { label: "of T3" }), "Autonomy tier earned",
        ov.autonomy.next_unlock),
      instrument(h("div", { class: "big" }, ov.calibration_n < 1000 ? String(ov.calibration_n) : ov.calibration_n, h("small", {}, " incidents")),
        "Calibration corpus", "the moat: grows with every resolved incident"),
      instrument(h("div", { class: "big" },
        ov.coverage?.marginal == null ? "—" : pct(ov.coverage.marginal), h("small", {}, ` / ${pct(1 - ov.alpha)} nominal`)),
        "Empirical coverage", "leave-one-out, per-tenant conformal guarantee"),
      instrument(h("div", { class: "big" }, ov.drift.energy_distance.toFixed(3)),
        "Drift energy distance", ov.drift.notes[0] || "")),
    h("div", { class: "grid deck-cols" },
      h("div", { class: "panel" },
        h("h3", {}, "Incident feed", h("span", { class: "r" }, `${open.length} active`)),
        h("div", {}, incidents.slice(0, 14).map((i) => feedItem(i)))),
      h("div", {},
        h("div", { class: "panel", style: "margin-bottom:16px" },
          h("h3", {}, net.world_title || "World map"),
          networkMap(net)),
        h("div", { class: "panel" },
          h("h3", {}, "Latest certificates"),
          certMiniList(ov)))));

  async function certMiniListLoad() {
    const certs = await api("/api/certificates");
    const box = $("#cert-mini", root); if (!box) return;
    box.innerHTML = "";
    certs.slice(0, 6).forEach((c) => box.append(
      h("div", { class: "feed-item", onclick: () => location.hash = `#/cert/${c.cert_id}` },
        h("span", { class: `verdict ${c.verdict}` }, c.verdict),
        h("div", { class: "t" },
          h("div", { class: "title mono", style: "font-size:11.5px" }, c.cert_id),
          h("div", { class: "sub" }, `${c.incident_id} · ${ago(c.created_at)}`)),
        c.pn != null ? h("span", { class: "chip" }, `PN ${c.pn.toFixed(2)}`) : null)));
  }
  function certMiniList() { const b = h("div", { id: "cert-mini" }, h("div", { class: "empty" }, "Loading…")); setTimeout(certMiniListLoad, 0); return b; }
  function instrument(vis, cap, sub) {
    return h("div", { class: "panel instrument" },
      vis instanceof SVGElement ? vis : vis, h("div", { class: "cap" }, cap),
      sub ? h("div", { class: "cap", style: "color:var(--ink-3);font-size:10px" }, sub) : null);
  }
}
function feedItem(i) {
  return h("div", { class: "feed-item", onclick: () => location.hash = `#/incident/${i.incident_id}` },
    h("span", { class: `sev ${i.severity}` }, i.severity),
    h("div", { class: "t" },
      h("div", { class: "title" }, i.title),
      h("div", { class: "sub" }, `${i.incident_id} · ${i.alarm_count} alarms · ${fmtD(i.t0)} ${fmtT(i.t0)}`)),
    h("span", { class: `status-word ${i.status}` }, i.status.toUpperCase()));
}

/* network map — generic: site positions come from the domain pack */
function networkMap(net) {
  const W = 460, H = 300;
  const impacted = new Set(net.impacted);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "impacted-anim" });
  const sites = net.entities.filter((e) => e.kind === "site");
  const SITE_XY = {};
  sites.forEach((site, i) => {
    const a = (i / Math.max(sites.length, 1)) * Math.PI * 2 - Math.PI / 2;
    SITE_XY[site.entity_id] = site.attrs.pos ||
      [0.5 + 0.38 * Math.cos(a), 0.5 + 0.38 * Math.sin(a)];
  });
  const XY = (site) => { const [fx, fy] = SITE_XY[site] || [0.5, 0.5]; return [40 + fx * (W - 80), 26 + fy * (H - 60)]; };
  const spans = net.entities.filter((e) => e.kind === "link");
  for (const sp of spans) {
    const [a, b] = sp.attrs.between || [];
    if (!SITE_XY[a] || !SITE_XY[b]) continue;
    const [x1, y1] = XY(a), [x2, y2] = XY(b);
    const mx = (x1 + x2) / 2 + (y2 - y1) * 0.12, my = (y1 + y2) / 2 - (x2 - x1) * 0.12;
    const path = s("path", { d: `M${x1},${y1} Q${mx},${my} ${x2},${y2}`,
      class: `map-span ${impacted.has(sp.entity_id) ? "impacted" : ""}` });
    path.addEventListener("mousemove", (ev) => showTip(ev, `<b>${esc(sp.entity_id)}</b> · ${esc(a)} ↔ ${esc(b)}${impacted.has(sp.entity_id) ? " · <span style='color:#F0938C'>impacted</span>" : ""}`));
    path.addEventListener("mouseleave", hideTip);
    svg.append(path);
  }
  for (const site of Object.keys(SITE_XY)) {
    const [x, y] = XY(site);
    const ents = net.entities.filter((e) => e.site === site && ["ne", "power"].includes(e.kind));
    const nEnt = Math.max(ents.length, 1);
    const anyImp = ents.some((e) => impacted.has(e.entity_id));
    svg.append(s("circle", { cx: x, cy: y, r: 13, class: `map-node ${anyImp ? "impacted" : ""}` }));
    if (anyImp) svg.append(s("circle", { cx: x, cy: y, r: 8, class: "map-pulse" }));
    svg.append(s("text", { x, y: y + 26, "text-anchor": "middle", class: "map-label" }, site));
    const g = s("g");
    ents.slice(0, 8).forEach((e, i) => {
      const a = (i / Math.min(nEnt, 8)) * Math.PI * 2 - Math.PI / 2;
      const dot = s("circle", { cx: x + 7 * Math.cos(a), cy: y + 7 * Math.sin(a), r: 2.2,
        fill: impacted.has(e.entity_id) ? "#C6423C" : "#8B9A93" });
      dot.addEventListener("mousemove", (ev) => showTip(ev, `<b>${esc(e.entity_id)}</b> · ${esc(e.layer)}`));
      dot.addEventListener("mouseleave", hideTip);
      g.append(dot);
    });
    svg.append(g);
  }
  return h("div", { class: "map-wrap" }, svg);
}

/* ── WAR ROOM ────────────────────────────────────────────────────────────── */
const STAGES = [
  ["substrate", "P1 · Substrate"], ["structure", "P2 · Structure"],
  ["hypotheses", "P3 · Hypotheses"], ["adjudication", "P4 · Adjudication"],
  ["calibration", "P5 · Calibration"], ["remediation", "P3′ · Remediation"],
  ["twin", "P4′ · Twin rollout"], ["gate", "P6 · Actuation gate"],
  ["certificate", "P7 · Certificate"]];

async function viewIncident(root, id) {
  const det = await api(`/api/incidents/${id}`);
  const inc = det.incident;
  renderNav("");
  const suppressed = det.instances.filter((i) => i.suppressed).length;

  const pipeline = h("div", { class: "pipeline" },
    STAGES.map(([key, label]) => h("div", { class: "stage", id: `st-${key}` },
      h("div", { class: "dot" }),
      h("div", { class: "body" }, h("div", { class: "name" }, label),
        h("div", { class: "detail", id: `st-${key}-d` }, "—")))));

  const hypBox = h("div", { id: "hyp-box" });
  const certBox = h("div", { id: "cert-box" });
  const verifyBtn = h("button", { class: "primary", onclick: runVerify },
    inc.status === "open" || inc.status === "verifying" ? "Run causal verification" : "Re-verify");

  root.append(
    h("div", { style: "display:flex;align-items:baseline;gap:14px;margin-bottom:2px" },
      h("h1", { class: "page" }, inc.title),
      h("span", { class: `sev ${inc.severity}`, style: "width:auto;padding:3px 8px" }, inc.severity)),
    h("div", { class: "page-sub" },
      h("span", { class: "mono" }, inc.incident_id), ` · ${inc.alarm_count} alarms across `,
      h("b", {}, `${new Set(det.instances.map((i) => i.layer).values()).size} layers`),
      ` · window ${((inc.t1 - inc.t0) / 60).toFixed(0)} min · `,
      h("span", { class: `status-word ${inc.status}` }, inc.status.toUpperCase()),
      inc.ground_truth ? h("span", {}, " · resolved root: ", h("span", { class: "mono", style: "color:var(--good)" }, inc.ground_truth)) : ""),
    h("div", { class: "grid war-grid" },
      h("div", { class: "panel" },
        h("h3", {}, "Verification pipeline"),
        verifyBtn, h("div", { style: "height:14px" }), pipeline),
      h("div", { class: "war-right" },
        h("div", { class: "panel" },
          h("h3", {}, "Alarm evidence ",
            h("span", { class: "r" }, `${det.instances.length} instances · ${suppressed} suppressed by Hawkes intensity`)),
          h("div", { class: "alarm-strip" },
            det.instances.map((i) => h("div", { class: `alarm-row ${i.suppressed ? "sup" : ""}` },
              h("span", { class: "ts" }, fmtT(i.ts)),
              h("span", { class: `etype lay-${i.layer || "env"}` }, i.type),
              h("span", { class: "ent" }, i.entity),
              h("span", { style: "margin-left:auto;color:var(--ink-3);font:10px var(--mono)" },
                i.alarms > 1 ? `×${i.alarms}` : "", ` ig ${i.info_gain.toFixed(2)}`))))),
        h("div", { class: "panel", id: "hyp-panel" },
          h("h3", {}, "Competing hypotheses ", h("span", { class: "r" }, "PN with 90% intervals")),
          hypBox.childElementCount ? hypBox : (hypBox.append(h("div", { class: "empty" }, "Run verification to adjudicate hypotheses against the causal model.")), hypBox)),
        certBox)));

  const prior = det.certificates;
  if (prior.length) renderCertSummary(certBox, prior[prior.length - 1]);

  if (inc.status !== "resolved") {
    const rootSel = h("select", { style: "flex:1;min-width:0;background:var(--panel-3);color:var(--ink);border:1px solid var(--hairline);border-radius:6px;padding:8px 10px;font:12px var(--mono)" },
      det.instances.filter((i) => !i.type.startsWith("svc."))
        .map((i) => h("option", { value: i.variable }, i.variable)));
    $(".war-right", root).append(h("div", { class: "panel" },
      h("h3", {}, "Close the loop"),
      h("div", { style: "font-size:12px;color:var(--ink-2);margin-bottom:10px" },
        "When this incident is over, record the verified root cause. Every label grows the calibration corpus — the guarantee is built from these."),
      h("div", { style: "display:flex;gap:10px" }, rootSel,
        h("button", { class: "primary", onclick: async () => {
          try {
            const res = await post(`/api/incidents/${inc.incident_id}/resolve`,
                                   { root_cause: rootSel.value, by: "operator" });
            toast(`Recorded. Calibration corpus: ${res.corpus_n} examples.`, "good");
            route();
          } catch (e) { toast(e.message, "crit"); }
        } }, "Record root cause & resolve"))));
  }

  function setStage(key, cls, detail) {
    const el = $(`#st-${key}`); if (!el) return;
    el.classList.remove("running", "done", "failed"); el.classList.add("on", cls);
    if (detail) $(`#st-${key}-d`).textContent = detail;
  }
  function runVerify() {
    verifyBtn.disabled = true;
    STAGES.forEach(([k]) => { const el = $(`#st-${k}`); el.className = "stage"; $(`#st-${k}-d`).textContent = "—"; });
    hypBox.innerHTML = ""; certBox.innerHTML = "";
    let prevKey = null;
    const es = new EventSource(withDomain(`/api/incidents/${id}/verify`));
    es.onmessage = (m) => {
      const step = JSON.parse(m.data);
      if (step.stage === "done") { es.close(); verifyBtn.disabled = false; refreshOverview(); return; }
      if (step.stage === "error") { toast(step.detail, "crit"); es.close(); verifyBtn.disabled = false; return; }
      if (prevKey && prevKey !== step.stage) setStage(prevKey, "done");
      setStage(step.stage, step.status === "running" ? "running" : (step.status === "failed" ? "failed" : "done"), step.detail);
      prevKey = step.stage;
      if (step.stage === "hypotheses" && step.data.hypotheses) renderHyps(step.data.hypotheses, null);
      if (step.stage === "adjudication" && step.data.adjudicated) renderHyps(null, step.data.adjudicated);
      if (step.stage === "certificate" && step.data.certificate) {
        renderCertSummary(certBox, step.data.certificate);
        toast(`Certificate issued: ${step.data.certificate.verdict}`,
          step.data.certificate.verdict === "SUPPORTED" ? "good" : "");
      }
    };
    es.onerror = () => { es.close(); verifyBtn.disabled = false; };
  }
  function renderHyps(hyps, adjudicated) {
    hypBox.innerHTML = "";
    if (adjudicated) {
      adjudicated.forEach((a, i) => hypBox.append(
        h("div", { class: `hyp ${i === 0 ? "top" : ""}` },
          h("div", { class: "h-row" },
            h("span", { class: "chip", style: "min-width:26px;justify-content:center" }, `#${i + 1}`),
            h("span", { class: "var" }, a.hypothesis.intervention.variable),
            h("span", { class: "src" }, a.hypothesis.source)),
          h("div", { class: "mech" }, a.hypothesis.mechanism),
          pnBar(a),
          h("div", { style: "display:flex;gap:8px;margin-top:8px;flex-wrap:wrap" },
            h("span", { class: "chip" }, `score ${(a.score * 100).toFixed(0)}%`),
            a.ps != null ? h("span", { class: "chip" }, `PS ${a.ps.toFixed(2)}`) : null,
            h("span", { class: `chip ${a.point_identified ? "" : "warn"}` },
              a.point_identified ? "point-identified" : "Tian–Pearl bounds"),
            a.refutations.length ? h("span", { class: `chip ${a.refutation_passed ? "good" : "crit"}` },
              a.refutation_passed ? "refuters ✓ 3/3" : "refutation failed") : null))));
    } else if (hyps) {
      hyps.forEach((hp, i) => hypBox.append(
        h("div", { class: "hyp" },
          h("div", { class: "h-row" },
            h("span", { class: "chip", style: "min-width:26px;justify-content:center" }, `#${i + 1}`),
            h("span", { class: "var" }, hp.intervention.variable),
            h("span", { class: "src" }, hp.source)),
          h("div", { class: "mech" }, hp.mechanism),
          h("div", { style: "color:var(--ink-3);font-size:11px" }, "adjudicating…"))));
    }
  }
}
function renderCertSummary(box, cert) {
  box.innerHTML = "";
  box.append(h("div", { class: "panel", style: "border-color:rgba(47,73,201,.35)" },
    h("h3", {}, "Causal certificate"),
    h("div", { style: "display:flex;align-items:center;gap:14px;flex-wrap:wrap" },
      h("span", { class: `verdict ${cert.verdict}` }, cert.verdict),
      h("span", { class: "mono", style: "font-size:12px;color:var(--ink-2)" }, cert.cert_id),
      cert.pn != null ? h("span", { class: "chip brass" }, `PN ${cert.pn.toFixed(2)} [${cert.pn_lo.toFixed(2)}, ${cert.pn_hi.toFixed(2)}]`) : null,
      h("span", { style: "flex:1" }),
      h("button", { class: "primary", onclick: () => location.hash = `#/cert/${cert.cert_id}` }, "Open certificate →")),
    h("div", { style: "margin-top:10px;color:var(--ink-2);font-size:12.5px" }, cert.decision)));
}

/* ── CERTIFICATES LIST ───────────────────────────────────────────────────── */
async function viewCerts(root) {
  const certs = await api("/api/certificates");
  root.append(h("h1", { class: "page" }, "Causal Certificates"),
    h("div", { class: "page-sub" }, `${certs.length} issued · every one signed Ed25519 and anchored in the transparency ledger`));
  const tbl = h("table", { class: "data" },
    h("thead", {}, h("tr", {}, ["Certificate", "Incident", "Claim", "Verdict", "PN", "Decision", "Issued"].map((c) => h("th", {}, c)))),
    h("tbody", {}, certs.map((c) => h("tr", { class: "click", onclick: () => location.hash = `#/cert/${c.cert_id}` },
      h("td", { class: "mono" }, c.cert_id.replace("keel:cert:", "")),
      h("td", { class: "mono" }, c.incident_id),
      h("td", { class: "mono", style: "max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, c.claim.root_cause || "—"),
      h("td", {}, h("span", { class: `verdict ${c.verdict}` }, c.verdict)),
      h("td", { class: "mono" }, c.pn != null ? c.pn.toFixed(2) : `[${c.pn_lo.toFixed(2)},${c.pn_hi.toFixed(2)}]`),
      h("td", { style: "font-size:11.5px;max-width:220px" }, c.decision.split("—")[0]),
      h("td", { class: "mono" }, ago(c.created_at))))));
  root.append(h("div", { class: "panel" }, tbl));
}

/* ── CERTIFICATE DOCUMENT (the paper artifact) ───────────────────────────── */
async function viewCert(root, certId) {
  const { certificate: c, verification: ver, inclusion_proof: proof, outcome } =
    await api(`/api/certificates/${certId}`);
  renderNav("certs");

  const ruler = (lo, hi, ptv) => {
    const r = h("div", { class: "ruler" }, h("div", { class: "base" }));
    for (let i = 0; i <= 10; i++) {
      r.append(h("div", { class: "tick", style: `left:${i * 10}%` }));
      if (i % 5 === 0) r.append(h("div", { class: "tick-lab", style: `left:${i * 10}%` }, (i / 10).toFixed(1)));
    }
    r.append(h("div", { class: "band", style: `left:${lo * 100}%;width:${Math.max(1, (hi - lo) * 100)}%` }));
    if (ptv != null) r.append(h("div", { class: "point", style: `left:calc(${ptv * 100}% - 5px)` }));
    return r;
  };
  const row = (k, v) => h("tr", {}, h("td", { class: "k" }, k), h("td", {}, v));

  const competing = (c.competing || []).slice(0, 5);
  const actions = [];
  const gateOk = c.gate && c.gate.decision !== "BLOCK";
  if (c.verdict === "SUPPORTED" && c.action && !outcome) {
    actions.push(h("button", { class: "primary", onclick: async () => {
      try {
        const res = await post(`/api/certificates/${c.cert_id}/execute`, { approver: "noc-operator" });
        toast(res.resolved ? "Remediation executed — incident resolved. Outcome recorded in calibration corpus."
          : "Executed — no effect observed. Outcome recorded honestly.", res.resolved ? "good" : "crit");
        route();
      } catch (e) { toast(e.message, "crit"); }
    } }, gateOk ? "Approve & execute remediation" : "Execution blocked by gate"));
  }

  root.append(
    h("div", { style: "display:flex;gap:12px;align-items:center;margin-bottom:18px" },
      h("button", { class: "ghost", onclick: () => history.back() }, "← Back"),
      h("span", { style: "flex:1" }), ...actions),
    h("div", { class: "paper" },
      h("div", { class: "head" },
        h("div", {},
          h("h2", {}, "CAUSAL CERTIFICATE"),
          h("div", { class: "cert-id" }, `${c.cert_id} · ${c.schema_version} · tenant ${c.tenant}`)),
        h("div", { class: `verdict-stamp ${c.verdict}` }, c.verdict)),
      h("hr", { class: "rule heavy" }),
      h("table", {},
        row("Incident", h("span", { class: "mono" }, `${c.incident_id}`)),
        row("Claimant", h("span", { class: "mono" }, c.claimant)),
        row("Claim — root cause", h("div", { class: "big-claim" }, c.claim.root_cause || "(abstention)")),
        c.claim.mechanism ? row("Mechanism", h("span", { class: "mono" }, c.claim.mechanism)) : null),
      h("div", { class: "sec-label" }, "Adjudication — counterfactual quantities"),
      h("table", {},
        row("Probability of necessity", c.pn != null
          ? h("div", {}, h("span", { class: "interval" }, `PN = ${c.pn.toFixed(2)}  [${c.pn_lo.toFixed(2)}, ${c.pn_hi.toFixed(2)}]  (90% bootstrap)`), ruler(c.pn_lo, c.pn_hi, c.pn))
          : h("div", {}, h("span", { class: "interval" }, `PN ∈ [${c.pn_lo.toFixed(2)}, ${c.pn_hi.toFixed(2)}]  — Tian–Pearl bounds`), ruler(c.pn_lo, c.pn_hi, null))),
        c.ps != null ? row("Probability of sufficiency", h("div", {},
          h("span", { class: "interval" }, `PS = ${c.ps.toFixed(2)}  [${c.ps_lo.toFixed(2)}, ${c.ps_hi.toFixed(2)}]`), ruler(c.ps_lo, c.ps_hi, c.ps))) : null,
        row("Identification", h("span", { class: "mono" }, c.identification || "n/a")),
        row("Evidence", h("span", {}, `${c.evidence_summary.alarms ?? "—"} alarms · ${(c.evidence_summary.layers || []).length} layers · ${c.evidence_summary.window_minutes ?? "—"} min window`))),
      competing.length > 1 ? h("div", {},
        h("div", { class: "sec-label" }, `Competing hypotheses — rank vs ${competing.length - 1} alternatives`),
        h("table", {}, competing.map((cc, i) => row(`#${i + 1} ${i === 0 ? "◈" : ""}`,
          h("span", { class: "mono" }, `${cc.variable}  ·  ${cc.pn != null ? "PN " + cc.pn.toFixed(2) : `PN∈[${cc.pn_lo.toFixed(2)},${cc.pn_hi.toFixed(2)}]`}  ·  score ${(cc.score * 100).toFixed(0)}%`))))) : null,
      c.refutation.length ? h("div", {},
        h("div", { class: "sec-label" }, "Refutation suite (mandatory)"),
        c.refutation.map((r) => h("div", { class: "refuter" },
          h("span", { class: `mark ${r.passed ? "pass" : "fail"}` }, r.passed ? "✓" : "✗"),
          h("span", { class: "mono", style: "width:190px;flex:none" }, r.refuter),
          h("span", { style: "color:var(--paper-ink-2);font-size:12px" }, r.detail)))) : null,
      h("div", { class: "sec-label" }, "Calibration & drift"),
      h("table", {},
        row("Conformal set", h("span", { class: "mono" },
          `{${(c.conformal.set || []).join(", ") || "∅"}} at α=${c.conformal.alpha} · q̂=${c.conformal.q_hat ?? "—"} · ${c.conformal.strata} · n=${c.conformal.n}`)),
        row("Drift gate", h("span", { class: "mono" },
          `${c.drift.level?.toUpperCase()} · energy ${c.drift.energy_distance} · graph Δ ${c.drift.graph_edit_distance} · fidelity residual ${c.drift.fidelity_residual}`))),
      c.action ? h("div", {},
        h("div", { class: "sec-label" }, "Proposed action — twin-simulated"),
        h("table", {},
          row("Action", h("div", {}, h("div", { class: "big-claim", style: "font-size:14.5px" }, c.action.description),
            h("span", { class: "mono", style: "color:var(--paper-ink-2)" }, `class=${c.action.action_class} · ${c.action.reversible ? "REVERSIBLE" : "NOT REVERSIBLE"} · ${c.action.rollback_plan}`))),
          c.twin ? row("Counterfactual outcome (twin T1)", h("span", { class: "mono" },
            `P(resolve)=${c.twin.p_resolve.toFixed(2)} · restore ${c.twin.restore_minutes} min [${c.twin.restore_lo}, ${c.twin.restore_hi}] · measured fidelity ${c.twin.fidelity_score}`)) : null,
          c.blast_radius ? row("Blast radius", h("span", { class: "mono" },
            `${c.blast_radius.elements.length} elements · ${c.blast_radius.services.length} services · ${c.blast_radius.slas_at_risk} SLAs at risk · ${c.blast_radius.customers_affected.toLocaleString()} customers exposed`)) : null,
          c.gate ? row("Gate", h("span", { class: "mono" },
            `${c.gate.decision} — ${c.gate.reason}${c.gate.projected ? " · shield projected action to safer variant" : ""}`)) : null)) : null,
      outcome ? h("div", {},
        h("div", { class: "sec-label" }, "Recorded outcome"),
        h("table", {}, row("Result", h("span", { class: "mono" },
          `${outcome.action_outcome.toUpperCase()} · SLA minutes lost ${outcome.sla_minutes_lost} · verified by ${outcome.verified_by}`)))) : null,
      h("hr", { class: "rule" }),
      h("div", { class: "seal-row" },
        h("div", { class: "sig-block" },
          h("div", {}, `DECISION   ${c.decision}`),
          h("div", {}, `versions   graph=${c.graph_version} · scm=${c.scm_version} · model=${c.model_version}`),
          h("div", {}, `signed     ${c.signer} · Ed25519 · ${new Date(c.created_at * 1000).toISOString()}`),
          h("div", {}, `signature  ${c.signature.slice(0, 48)}…`),
          h("div", { class: ver.signature_valid ? "ok" : "" },
            ver.signature_valid ? "✓ SIGNATURE VERIFIED against authority public key" : "✗ SIGNATURE INVALID"),
          proof ? h("div", {}, `ledger     leaf #${proof.index} of ${proof.size} · root ${proof.root.slice(0, 24)}…`) : null),
        h("div", { class: "seal" }, `KEEL\n${c.verdict}\n${new Date(c.created_at * 1000).toISOString().slice(0, 10)}`))));
}

/* ── CAUSAL ATLAS ────────────────────────────────────────────────────────── */
async function viewAtlas(root) {
  const g = await api("/api/graph");
  const active = g.edges.filter((e) => e.provenance !== "expert_vetoed");
  root.append(h("h1", { class: "page" }, "Causal Atlas"),
    h("div", { class: "page-sub" },
      "Type-level causal structure learned from ", h("b", {}, "topology-constrained Hawkes discovery"),
      ` with stability selection · version `, h("b", { class: "mono" }, g.version),
      ` · ${active.length} active edges · edge width = strength, opacity = bootstrap stability`));

  const nodes = [...new Set(g.edges.flatMap((e) => [e.src_type, e.dst_type]))];
  const W = 860, H = 560;
  const pos = {}; const vel = {};
  nodes.forEach((n, i) => {
    const a = (i / nodes.length) * Math.PI * 2;
    pos[n] = [W / 2 + 200 * Math.cos(a), H / 2 + 180 * Math.sin(a)]; vel[n] = [0, 0];
  });
  const edgesBy = active.map((e) => [e.src_type, e.dst_type, e]);
  for (let it = 0; it < 260; it++) {
    for (const a of nodes) for (const b of nodes) {
      if (a >= b) continue;
      const dx = pos[b][0] - pos[a][0], dy = pos[b][1] - pos[a][1];
      const d2 = Math.max(dx * dx + dy * dy, 120);
      const f = 10500 / d2, dl = Math.sqrt(d2);
      vel[a][0] -= (f * dx) / dl; vel[a][1] -= (f * dy) / dl;
      vel[b][0] += (f * dx) / dl; vel[b][1] += (f * dy) / dl;
    }
    for (const [a, b] of edgesBy) {
      const dx = pos[b][0] - pos[a][0], dy = pos[b][1] - pos[a][1];
      const d = Math.max(Math.hypot(dx, dy), 1);
      const f = (d - 165) * 0.012;
      vel[a][0] += (f * dx) / d; vel[a][1] += (f * dy) / d;
      vel[b][0] -= (f * dx) / d; vel[b][1] -= (f * dy) / d;
    }
    for (const n of nodes) {
      vel[n][0] += (W / 2 - pos[n][0]) * 0.0022; vel[n][1] += (H / 2 - pos[n][1]) * 0.0022;
      pos[n][0] = Math.max(58, Math.min(W - 58, pos[n][0] + vel[n][0] * 0.5));
      pos[n][1] = Math.max(30, Math.min(H - 30, pos[n][1] + vel[n][1] * 0.5));
      vel[n][0] *= 0.72; vel[n][1] *= 0.72;
    }
  }
  const layerCol = (t) => t.startsWith("optical") ? "#C05621" : t.startsWith("svc") ? "#A8438F"
    : t.startsWith("power") || t.startsWith("hw") ? "#B06E10" : t.startsWith("ran") ? "#7C52C7"
    : t.startsWith("mpls") || t.startsWith("ldp") ? "#0B7A5A" : "#2F49C9";

  const side = h("div", { class: "panel" }, h("h3", {}, "Edge inspector"),
    h("div", { class: "empty" }, "Select an edge to inspect strength, lags, and provenance — or to pin / veto it as a domain expert."));
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "atlas-svg" });
  const defs = s("defs", {},
    s("marker", { id: "arr", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: "userSpaceOnUse", orient: "auto-start-reverse" },
      s("path", { d: "M0,0L10,5L0,10z", fill: "#96A29B" })));
  svg.append(defs);
  for (const e of g.edges) {
    const [x1, y1] = pos[e.src_type] || [0, 0], [x2, y2] = pos[e.dst_type] || [0, 0];
    const mx = (x1 + x2) / 2 + (y2 - y1) * 0.08, my = (y1 + y2) / 2 - (x2 - x1) * 0.08;
    const line = s("path", { d: `M${x1},${y1} Q${mx},${my} ${x2},${y2}`,
      class: `edge-line ${e.provenance}`, "stroke-width": (1 + 4 * e.strength).toFixed(1),
      opacity: (0.35 + 0.65 * e.stability).toFixed(2), "marker-end": "url(#arr)" });
    line.addEventListener("mousemove", (ev) => showTip(ev,
      `<b>${esc(e.src_type)} → ${esc(e.dst_type)}</b><br>strength ${e.strength.toFixed(2)} · stability ${(e.stability * 100).toFixed(0)}%<br>lag ${(e.lag_lo_ms / 1000).toFixed(0)}–${(e.lag_hi_ms / 1000).toFixed(0)}s · ${esc(e.provenance)}`));
    line.addEventListener("mouseleave", hideTip);
    line.addEventListener("click", () => inspect(e, line));
    svg.append(line);
  }
  for (const n of nodes) {
    const [x, y] = pos[n];
    const grp = s("g", { class: `gnode ${n.startsWith("svc") ? "svc" : ""}` });
    grp.append(s("circle", { cx: x, cy: y, r: 7, fill: "#FFFFFF", stroke: layerCol(n) }));
    grp.append(s("text", { x: x + 10, y: y + 3.5 }, n));
    svg.append(grp);
  }
  function inspect(e, line) {
    svg.querySelectorAll(".edge-line.sel").forEach((el) => el.classList.remove("sel"));
    line.classList.add("sel");
    side.innerHTML = "";
    side.append(h("h3", {}, "Edge inspector"),
      h("div", { class: "mono", style: "font-size:13px;color:var(--ink);margin-bottom:10px" }, `${e.src_type} → ${e.dst_type}`),
      h("table", { class: "data" },
        h("tr", {}, h("td", {}, "strength"), h("td", { class: "mono" }, e.strength.toFixed(3))),
        h("tr", {}, h("td", {}, "stability"), h("td", { class: "mono" }, `${(e.stability * 100).toFixed(0)}% of bootstraps`)),
        h("tr", {}, h("td", {}, "lag window"), h("td", { class: "mono" }, `${(e.lag_lo_ms / 1000).toFixed(1)}s – ${(e.lag_hi_ms / 1000).toFixed(1)}s`)),
        h("tr", {}, h("td", {}, "method"), h("td", { class: "mono" }, e.method)),
        h("tr", {}, h("td", {}, "provenance"), h("td", {}, h("span", { class: `chip ${e.provenance === "expert_pinned" ? "brass" : e.provenance === "expert_vetoed" ? "crit" : ""}` }, e.provenance))),
        e.pinned_by ? h("tr", {}, h("td", {}, "by"), h("td", { class: "mono" }, `${e.pinned_by}: ${e.pinned_reason}`)) : null),
      h("div", { style: "display:flex;gap:8px;margin-top:14px" },
        h("button", { onclick: () => editEdge(e, "pin") }, "Pin as expert"),
        h("button", { class: "danger", onclick: () => editEdge(e, "veto") }, "Veto"),
        h("button", { class: "ghost", onclick: () => editEdge(e, "clear") }, "Clear")),
      h("div", { style: "margin-top:12px;font-size:11px;color:var(--ink-3)" },
        "Pins and vetoes are first-class inputs with provenance; the graph republishes as a new version and every future certificate references it."));
  }
  async function editEdge(e, action) {
    const reason = action === "clear" ? "" : prompt(`Reason for ${action} (${e.src_type} → ${e.dst_type}):`, "domain knowledge") || "";
    if (reason === null) return;
    const res = await post("/api/graph/pin", { src: e.src_type, dst: e.dst_type, action, reason, by: "operator" });
    toast(`Graph republished as ${res.version}`, "good");
    route();
  }
  root.append(h("div", { class: "atlas-wrap" }, h("div", {}, svg,
    h("div", { class: "legend" },
      h("span", { class: "it" }, h("span", { class: "sw", style: "background:#A9B6C8" }), "learned"),
      h("span", { class: "it" }, h("span", { class: "sw", style: "background:var(--brass)" }), "expert-pinned"),
      h("span", { class: "it" }, h("span", { class: "sw", style: "background:#D8A29E" }), "vetoed"),
      h("span", { class: "it", style: "margin-left:auto;color:var(--ink-3)" }, `graph history: ${g.history.length} versions`))), side));
}

/* ── CALIBRATION ─────────────────────────────────────────────────────────── */
async function viewCalibration(root) {
  const cal = await api("/api/calibration");
  root.append(h("h1", { class: "page" }, "Calibration Observatory"),
    h("div", { class: "page-sub" }, "The guarantee is only as good as the corpus it is derived from. ",
      h("b", {}, `n=${cal.n}`), ` resolved incidents · α=${cal.alpha} · re-derived continuously from this tenant's own outcomes`));

  const hist = new Array(10).fill(0);
  cal.corpus.forEach((c) => { hist[Math.min(9, Math.floor((1 - c.score_true) * 10))]++; });
  const covSeries = [];
  let hit = 0;
  cal.corpus.forEach((c, i) => { if (1 - c.score_true <= 0.5) hit++; covSeries.push([i + 1, hit / (i + 1)]); });

  const strata = Object.entries(cal.coverage.per_stratum || {});
  const fid = cal.fidelity || [];
  const byClass = {};
  fid.forEach((f) => { (byClass[f.action_class] ??= []).push(f.err); });

  root.append(h("div", { class: "grid", style: "grid-template-columns:1.2fr 1fr" },
    h("div", { class: "panel" },
      h("h3", {}, "Empirical coverage (leave-one-out) ", h("span", { class: "r" },
        `marginal ${pct(cal.coverage.marginal, 1)} vs nominal ${pct(cal.coverage.nominal ?? 0.9)}`)),
      lineChart([{ points: covSeries, color: SERIES[0], label: "coverage" }],
        { x1: Math.max(covSeries.length, 2), y0: 0.5, y1: 1.0, refY: 0.9, refLabel: "nominal 1−α",
          xfmt: (v) => `${v | 0}`, yfmt: (v) => v.toFixed(2) }),
      h("div", { style: "margin-top:14px" }),
      h("h3", {}, "Per-stratum coverage (Mondrian)"),
      strata.length ? barsH(strata.map(([k, v]) => ({
        label: `${k} (n=${v.n})`, value: v.coverage, color: SERIES[0] })),
        { max: 1, fmt: (v) => pct(v, 1) }) : h("div", { class: "empty" }, "No strata yet")),
    h("div", {},
      h("div", { class: "panel", style: "margin-bottom:16px" },
        h("h3", {}, "Nonconformity distribution"),
        barsH(hist.map((v, i) => ({ label: `${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}`, value: v, color: "#6E8091" })),
          { fmt: (v) => String(v), barH: 16, gap: 6 }),
        h("div", { style: "font-size:11px;color:var(--ink-3);margin-top:8px" },
          "score = 1 − p̂(true root). The q̂ quantile of this distribution is the bar every live hypothesis must clear.")),
      h("div", { class: "panel" },
        h("h3", {}, "Twin fidelity ledger ", h("span", { class: "r" }, "per action class · |predicted − observed|")),
        Object.keys(byClass).length ? barsH(Object.entries(byClass).map(([k, errs]) => ({
          label: k, value: errs.reduce((a, b) => a + b, 0) / errs.length,
          extra: ` · ${errs.length} executions`, color: "#6E8091" })),
          { max: 1, fmt: (v) => v.toFixed(2) }) : h("div", { class: "empty" }, "No executions recorded yet"),
        h("div", { style: "font-size:11px;color:var(--ink-3);margin-top:8px" },
          "Action classes whose residual exceeds the floor are refused certification. Fidelity is measured, never assumed."))),
  ));

  const d = cal.drift;
  root.append(h("div", { class: "panel", style: "margin-top:16px" },
    h("h3", {}, "Drift gate ", h("span", { class: "r" }, d.level.toUpperCase())),
    h("div", { class: "grid", style: "grid-template-columns:repeat(3,1fr)" },
      driftDial("Energy distance", d.energy_distance, 0.30, "recent vs calibration features"),
      driftDial("Graph edit distance", d.graph_edit_distance, 0.35, "between successive versions"),
      driftDial("Fidelity residual", d.fidelity_residual, 0.50, "rolling twin error")),
    h("div", { style: "margin-top:10px;font-size:12px;color:var(--ink-2)" }, d.notes.join(" · "))));
  function driftDial(name, v, breach, sub) {
    const frac = Math.min(1, v / breach);
    const col = frac < 0.5 ? "#178A50" : frac < 1 ? "#B06E10" : "#C6423C";
    return h("div", { style: "text-align:center" },
      arcGauge(+v.toFixed(3), breach, { label: `breach at ${breach}`, color: col, w: 150 }),
      h("div", { class: "cap", style: "margin-top:4px" }, name),
      h("div", { class: "cap", style: "color:var(--ink-3);font-size:10px" }, sub));
  }
}

/* ── LEDGER ──────────────────────────────────────────────────────────────── */
async function viewLedger(root) {
  const lg = await api("/api/translog");
  const chainOk = lg.chain.consistent;
  root.append(h("h1", { class: "page" }, "Transparency Ledger"),
    h("div", { class: "page-sub" }, "Append-only Merkle log. Every certificate is a leaf; altering any historical certificate breaks the chain — which is what makes a KEEL certificate admissible in a postmortem or an audit."),
    h("div", { class: "chain-banner" },
      h("span", { class: `chip ${chainOk ? "good" : "crit"}`, style: "font-size:11.5px;padding:6px 14px" },
        chainOk ? `✓ chain consistent · ${lg.chain.size} leaves` : `✗ TAMPERING DETECTED — ${lg.chain.violations.length} violations`),
      h("span", { class: "hash" }, "root ", h("b", {}, lg.root))));
  const detail = h("div", {});
  root.append(h("div", { class: "grid", style: "grid-template-columns:1.4fr 1fr" },
    h("div", { class: "panel" },
      h("h3", {}, "Log entries"),
      h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ["#", "Leaf hash", "Certificate", "Anchored"].map((c) => h("th", {}, c)))),
        h("tbody", {}, lg.entries.slice().reverse().map((e) => h("tr", { class: "click", onclick: () => showProof(e) },
          h("td", { class: "mono" }, String(e.idx)),
          h("td", { class: "mono" }, e.leaf_hash.slice(0, 22) + "…"),
          h("td", { class: "mono" }, e.cert_id.replace("keel:cert:", "")),
          h("td", { class: "mono" }, ago(e.ts))))))),
    h("div", { class: "panel" }, h("h3", {}, "Inclusion proof"), detail,
      h("div", { class: "empty", id: "proof-empty" }, "Select a leaf to compute its Merkle inclusion proof."))));
  async function showProof(e) {
    const p = await api(`/api/translog/${e.idx}/proof`);
    detail.innerHTML = ""; const pe = $("#proof-empty"); if (pe) pe.remove();
    detail.append(
      h("div", { class: "hash", style: "margin-bottom:6px" }, "leaf ", h("b", {}, p.leaf.slice(0, 30) + "…")),
      h("div", { class: "proof-path" }, p.path.map((st, i) => h("div", { class: "proof-step" },
        h("span", { class: "side" }, st.side.toUpperCase()),
        h("span", {}, `⊕ ${st.hash.slice(0, 26)}…`))),
        h("div", { class: "proof-step", style: "margin-top:6px" },
          h("span", { class: "side" }, "ROOT"), h("span", { style: "color:var(--good)" }, `= ${p.root.slice(0, 26)}… ✓`))),
      h("div", { style: "margin-top:10px;font-size:11px;color:var(--ink-3)" },
        `path length ${p.path.length} · tree size ${p.size} · anyone holding the root can verify this certificate existed, unaltered, at issuance.`));
  }
}

/* ── EVIDENCE (evaluation) ───────────────────────────────────────────────── */
async function viewEvidence(root) {
  let rep = await api("/api/eval/report");
  root.append(h("h1", { class: "page" }, "Evidence"),
    h("div", { class: "page-sub" }, "Retrospective replay on held-out resolved incidents — the report a buyer's risk committee asks for. Falsifiable, on this tenant's own data."),
    h("div", { style: "margin-bottom:16px" },
      h("button", { class: "primary", onclick: async (ev) => {
        ev.target.disabled = true; ev.target.textContent = "Replaying…";
        rep = await post("/api/eval/run"); toast("Replay complete", "good"); route();
      } }, rep.status === "not_run" ? "Run retrospective replay" : "Re-run replay")));
  if (rep.status === "not_run") { root.append(h("div", { class: "empty" }, "No report yet — run the replay.")); return; }

  const methods = [
    ["KEEL (causal + conformal)", rep.keel, SERIES[0]],
    ["severity-first triage", rep.baseline_severity, SERIES[2]],
    ["correlation + PageRank", rep.baseline_corr_pagerank, SERIES[3]]];
  const metricRows = (key) => methods.map(([label, m, color]) => ({ label, value: m[key], color, extra: ` · n=${m.n}` }));

  root.append(h("div", { class: "grid", style: "grid-template-columns:1fr 1fr" },
    h("div", { class: "panel" },
      h("h3", {}, "Top-1 hit rate (HR@1) ", h("span", { class: "r" }, `Δ vs best baseline: +${(rep.beats_baseline_by * 100).toFixed(0)} pts`)),
      barsH(metricRows("hr1"), { max: 1, fmt: (v) => pct(v, 1), w: 470, labW: 200 }),
      h("div", { style: "height:12px" }),
      h("h3", {}, "Mean reciprocal rank"),
      barsH(metricRows("mrr"), { max: 1, fmt: (v) => v.toFixed(3), w: 470, labW: 200 }),
      h("div", { class: "legend" }, methods.map(([label, , color]) =>
        h("span", { class: "it" }, h("span", { class: "sw", style: `background:${color}` }), label)))),
    h("div", {},
      h("div", { class: "panel", style: "margin-bottom:16px" },
        h("h3", {}, "Risk–coverage (selective prediction)"),
        lineChart([{ points: rep.risk_coverage.map((r) => [r.coverage, r.accuracy]), color: SERIES[1], label: "top-1 accuracy" }],
          { x0: 0.2, x1: 1.0, y0: 0.5, y1: 1.0, xfmt: (v) => pct(v), yfmt: (v) => pct(v) }),
        h("div", { style: "font-size:11px;color:var(--ink-3);margin-top:6px" },
          `Abstention rate ${pct(rep.abstention_rate, 1)} — published, not hidden. Accuracy on the answered set is the number that matters.`)),
      h("div", { class: "panel" },
        h("h3", {}, "Conformal coverage on holdout"),
        h("table", { class: "data" },
          h("tr", {}, h("td", {}, "empirical"), h("td", { class: "mono" }, pct(rep.coverage.empirical, 1))),
          h("tr", {}, h("td", {}, "nominal (1−α)"), h("td", { class: "mono" }, pct(rep.coverage.nominal))),
          ...Object.entries(rep.coverage.per_stratum || {}).map(([k, v]) =>
            h("tr", {}, h("td", {}, `stratum: ${k}`), h("td", { class: "mono" }, `${pct(v.coverage, 1)} (n=${v.n})`)))),
        h("div", { style: "font-size:11px;color:var(--ink-3);margin-top:8px" },
          `holdout ${rep.holdout_n} incidents · replayed in ${rep.replay_seconds}s · generated ${ago(rep.generated_at)}`)))));
}

/* ── POLICY ──────────────────────────────────────────────────────────────── */
async function viewPolicy(root) {
  const p = await api("/api/policy");
  const auto = p.autonomy;
  root.append(h("h1", { class: "page" }, "Policy & Autonomy"),
    h("div", { class: "page-sub" }, "Safety (CMDP shield) is physics; policy is permission. Tiers are ",
      h("b", {}, "earned from this tenant's own outcome history"), ", never granted by configuration."));
  root.append(h("div", { class: "grid", style: "grid-template-columns:1.5fr 1fr" },
    h("div", {},
      Object.values(p.tiers).map((t) => h("div", { class: `tier-row ${auto.tier === t.tier ? "active" : ""}` },
        h("div", { class: "tname" }, t.name, auto.tier === t.tier ? h("span", { class: "chip brass", style: "margin-left:8px" }, "current") : null),
        h("div", { class: "tdesc" }, t.behavior),
        h("div", { class: "treq" },
          `PN_lo ≥ ${t.pn_lower_min} · blast ≤ ${t.max_blast_elements} · SLAs ≤ ${t.max_slas_at_risk}`,
          h("br"), `${t.requires_reversible ? "reversible required" : "irreversible allowed"} · ${t.min_prior_successes}+ prior successes`))),
      h("div", { class: "panel", style: "margin-top:6px" },
        h("h3", {}, "Path to next tier"),
        h("div", { style: "font-size:13px;color:var(--ink-2)" }, auto.next_unlock),
        h("div", { style: "margin-top:8px" },
          h("span", { class: "chip" }, `corpus ${auto.corpus_n}`),
          " ", h("span", { class: "chip" }, `executed ${auto.executed}`),
          " ", h("span", { class: "chip good" }, `success rate ${pct(auto.success_rate, 1)}`)))),
    h("div", {},
      h("div", { class: "panel", style: "margin-bottom:16px" },
        h("h3", {}, "CMDP constraint limits"),
        h("table", { class: "data" }, Object.entries(p.cmdp_limits).map(([k, v]) =>
          h("tr", {}, h("td", {}, k.replaceAll("_", " ")), h("td", { class: "mono" }, String(v)))))),
      h("div", { class: "panel", style: "margin-bottom:16px" },
        h("h3", {}, "Change windows (local hours)"),
        h("div", { class: "mono", style: "font-size:13px" }, p.change_windows.map(([a, b]) => `${String(a).padStart(2, "0")}:00–${String(b).padStart(2, "0")}:00`).join("  ·  "))),
      h("div", { class: "panel" },
        h("h3", {}, "Operator override"),
        h("div", { style: "font-size:12px;color:var(--ink-2);margin-bottom:10px" }, "Cap the maximum tier this deployment may exercise, regardless of what has been earned."),
        h("div", { style: "display:flex;gap:8px" }, [0, 1, 2, 3].map((t) =>
          h("button", { class: (p.overrides.max_tier ?? 3) === t ? "primary" : "",
            onclick: async () => { await api("/api/policy/overrides", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ max_tier: t }) }); toast(`Max tier capped at T${t}`); route(); } }, `T${t}`)))))));
}

/* ── CONNECT YOUR DATA (onboarding) ─────────────────────────────────────── */
const EX_TOPO = `{
  "entities": [
    {"entity_id": "db-primary",  "kind": "ne",      "layer": "data"},
    {"entity_id": "api-svc",     "kind": "ne",      "layer": "app"},
    {"entity_id": "host-a",      "kind": "power",   "layer": "infra"},
    {"entity_id": "checkout",    "kind": "service", "layer": "service",
     "attrs": {"customers": 250000, "paths": [["api-svc", "db-primary"]]}}
  ],
  "edges": [
    {"src": "db-primary", "dst": "api-svc",  "relation": "carries"},
    {"src": "host-a",     "dst": "api-svc",  "relation": "feeds"},
    {"src": "api-svc",    "dst": "checkout", "relation": "serves"}
  ]
}`;
const EX_EVENTS = `[
  {"entity": "db-primary", "type": "db_pool_exhausted", "severity": 1, "ts": "2026-08-14T02:11:05Z"},
  {"entity": "api-svc",    "type": "latency_p99_high",  "severity": 2, "ts": "2026-08-14T02:12:40Z"},
  {"entity": "checkout",   "type": "checkout_outage",   "severity": 1, "ts": "2026-08-14T02:15:02Z"}
]`;
const EX_LABELS = `[
  {"t0": "2026-08-14T02:10:00Z", "t1": "2026-08-14T02:40:00Z",
   "root_cause_entity": "db-primary", "root_cause_type": "db_pool_exhausted",
   "title": "Checkout outage — connection pool"}
]`;

function jparse(text) {
  const t = text.trim();
  if (!t) throw new Error("nothing to parse");
  try { return JSON.parse(t); } catch { /* try NDJSON */ }
  return t.split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
}
const ta = (ph) => h("textarea", { class: "ing", placeholder: ph, spellcheck: "false" });

async function viewConnect(root) {
  PACKS = await fetch("/api/domains").then((r) => r.json());
  const mine = PACKS.filter((p) => !p.sandbox);
  const current = PACKS.find((p) => p.key === DOM);
  const manageKey = current && !current.sandbox ? DOM : null;

  root.append(h("h1", { class: "page" }, "Connect your data"),
    h("div", { class: "page-sub" },
      "KEEL runs on ", h("b", {}, "your data, in your vocabulary"),
      " — any field, any company. Create a workspace, connect events and topology, tell KEEL what impact means to you, and it learns your causal model. The sandboxes are demos; this is the product."));

  // ── create ────────────────────────────────────────────────────────────────
  const nameIn = h("input", { class: "ing-line", placeholder: "Workspace name — e.g. Acme Logistics Prod" });
  const tenantIn = h("input", { class: "ing-line", placeholder: "Tenant id (optional)" });
  const autoCk = h("input", { type: "checkbox", checked: "" });
  root.append(h("div", { class: "panel", style: "margin-bottom:16px" },
    h("h3", {}, manageKey ? "Create another workspace" : "1 · Create a workspace"),
    h("div", { style: "display:flex;gap:10px;flex-wrap:wrap;align-items:center" },
      nameIn, tenantIn,
      h("label", { style: "display:flex;gap:6px;align-items:center;font-size:12px;color:var(--ink-2)" },
        autoCk, "auto-verify detected incidents"),
      h("button", { class: "primary", onclick: async () => {
        if (!nameIn.value.trim()) return toast("Give the workspace a name", "crit");
        const ws = await post("/api/workspaces", { name: nameIn.value.trim(),
          tenant: tenantIn.value.trim(),
          profile: { auto_verify: autoCk.checked } });
        DOM = ws.key;
        localStorage.setItem("keel-domain", DOM);
        toast(`Workspace ${ws.name} created`, "good");
        await initDomains(); route();
      } }, "Create workspace")),
    mine.length && !manageKey ? h("div", { style: "margin-top:12px;font-size:12px;color:var(--ink-2)" },
      "…or open an existing workspace: ",
      mine.map((w) => h("button", { class: "ghost", style: "margin-right:6px",
        onclick: async () => { DOM = w.key; localStorage.setItem("keel-domain", DOM);
          await initDomains(); route(); } }, `◆ ${w.name}`))) : null));
  if (!manageKey) return;

  // ── manage ────────────────────────────────────────────────────────────────
  const ws = await api(`/api/workspaces/${manageKey}`);
  const st = ws.status;
  const chip = (label, v, ok) => h("span", { class: `chip ${ok ? "good" : "warn"}`,
    style: "font-size:11px;padding:5px 11px" }, `${label} ${v}`);
  root.append(h("div", { class: "panel", style: "margin-bottom:16px" },
    h("h3", {}, `Workspace · ${ws.name} `, h("span", { class: "r mono" }, manageKey)),
    h("div", { style: "display:flex;gap:8px;flex-wrap:wrap" },
      chip("entities", st.entities, st.entities > 0),
      chip("topology edges", st.topology_edges, st.topology_edges > 0),
      chip("events", st.events, st.events > 0),
      chip("incidents", st.incidents, st.incidents > 0),
      chip("labeled", st.labeled, st.labeled >= 25),
      chip("graph edges", st.graph_edges, st.graph_edges > 0),
      chip("calibration", st.calibration_n, st.calibration_n >= 25)),
    h("div", { style: "margin-top:10px;font-size:11.5px;color:var(--ink-3)" },
      st.calibration_n >= 25
        ? "Calibration active — conformal guarantees are live for this tenant."
        : `Guarantees activate at 25 labeled incidents (have ${st.labeled}). Until then KEEL certifies conservatively and abstains honestly.`)));

  const ingestPanel = (title, hint, example, submit) => {
    const box = ta(example);
    return h("div", { class: "panel", style: "margin-bottom:16px" },
      h("h3", {}, title), h("div", { class: "ing-hint" }, hint), box,
      h("div", { style: "margin-top:10px;display:flex;gap:10px;align-items:center" },
        h("button", { class: "primary", onclick: async (ev) => {
          try {
            ev.target.disabled = true;
            const res = await submit(jparse(box.value));
            toast(JSON.stringify(res).slice(0, 160), "good");
            route();
          } catch (e) { toast(String(e.message || e), "crit"); ev.target.disabled = false; }
        } }, "Ingest"),
        h("span", { style: "font-size:11px;color:var(--ink-3)" }, "JSON or NDJSON · pasted here or POSTed to the same endpoint")));
  };

  root.append(h("div", { class: "grid", style: "grid-template-columns:1fr 1fr" },
    h("div", {},
      ingestPanel("2 · Topology — your systems and dependencies",
        "kind 'service' entities carry attrs.paths for SLA/blast analysis; kind 'power' marks shared infrastructure (the hidden-common-cause class). Skip entirely and KEEL infers adjacency from event co-occurrence — uploading real topology always beats inference.",
        EX_TOPO, (b) => post(`/api/ingest/topology`, b)),
      ingestPanel("4 · Labeled history — your postmortems (the moat)",
        "Resolved incidents with verified root causes. These build the calibration corpus that makes the statistical guarantee YOURS. 25+ activates conformal certification; more is better.",
        EX_LABELS, (b) => post(`/api/ingest/incidents`, b))),
    h("div", {},
      ingestPanel("3 · Events — alarms, alerts, logs",
        "Historical bulk + live stream, same endpoint. Live bursts are detected as incidents autonomously (sessionization) and — if auto-verify is on — certified without a human in the loop.",
        EX_EVENTS, (b) => post(`/api/ingest/events`, b)),
      h("div", { class: "panel", style: "margin-bottom:16px" },
        h("h3", {}, "Live integrations"),
        h("div", { class: "ing-hint" }, "Point Prometheus Alertmanager at:"),
        h("div", { class: "mono", style: "font-size:11px;color:var(--ink);background:var(--panel-3);padding:8px 10px;border-radius:5px;user-select:all" },
          `${location.origin}/api/webhook/alertmanager?domain=${manageKey}`),
        h("div", { class: "ing-hint", style: "margin-top:8px" },
          "Generic events endpoint (any agent, any script, MCP, A2A):"),
        h("div", { class: "mono", style: "font-size:11px;color:var(--ink);background:var(--panel-3);padding:8px 10px;border-radius:5px;user-select:all" },
          `POST ${location.origin}/api/ingest/events?domain=${manageKey}`)))));

  // ── vocabulary ────────────────────────────────────────────────────────────
  const types = await api(`/api/workspaces/${manageKey}/types`);
  const prof = ws.profile;
  const roleOf = (t) => prof.outage_types.includes(t) ? "outage"
    : prof.degradation_types.includes(t) ? "degradation"
    : prof.change_types.includes(t) ? "change"
    : prof.hard_down_types.includes(t) ? "hard-down"
    : prof.confounder_types.includes(t) ? "confounder" : "";
  const rows = types.types.map((t) => {
    const sel = h("select", { class: "ing-line", style: "padding:4px 8px;font-size:11.5px" },
      ["", "outage", "degradation", "change", "hard-down", "confounder"].map((o) =>
        h("option", { value: o }, o || "— not special —")));
    sel.value = roleOf(t.type) || t.suggest || "";
    return { t, sel };
  });
  root.append(h("div", { class: "panel", style: "margin-top:0" },
    h("h3", {}, "5 · Vocabulary — what does impact mean in YOUR data? ",
      h("span", { class: "r" }, `${types.types.length} observed types · suggestions prefilled, you decide`)),
    types.types.length === 0
      ? h("div", { class: "empty" }, "Ingest events first — observed types appear here.")
      : h("div", { class: "vocab-grid" }, rows.map(({ t, sel }) =>
          h("div", { class: "vocab-row" },
            h("span", { class: "mono", style: "flex:1;overflow:hidden;text-overflow:ellipsis" }, t.type),
            h("span", { style: "color:var(--ink-3);font-size:10.5px;width:46px;text-align:right" }, `×${t.count}`),
            sel))),
    h("div", { style: "margin-top:14px;display:flex;gap:10px;align-items:center" },
      h("button", { class: "primary", onclick: async () => {
        const buckets = { outage: [], degradation: [], change: [], "hard-down": [], confounder: [] };
        rows.forEach(({ t, sel }) => { if (sel.value) buckets[sel.value].push(t.type); });
        await api(`/api/workspaces/${manageKey}/profile`, { method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ outage_types: buckets.outage,
            degradation_types: buckets.degradation, change_types: buckets.change,
            hard_down_types: buckets["hard-down"],
            confounder_types: buckets.confounder }) });
        toast("Vocabulary saved — profile updated", "good");
      } }, "Save vocabulary"),
      h("span", { style: "font-size:11px;color:var(--ink-3)" },
        "outage/degradation define the OUTCOME the causal engine explains; change events are exogenous; hard-down kills path liveness; confounder types signal hidden shared-infrastructure failures."))));

  // ── learn ─────────────────────────────────────────────────────────────────
  const learnOut = h("div", { class: "mono", style: "font-size:11.5px;color:var(--ink-2);margin-top:10px;white-space:pre-wrap" });
  root.append(h("div", { class: "panel", style: "margin-top:16px" },
    h("h3", {}, "6 · Learn"),
    h("div", { class: "ing-hint" },
      "Windows all history into incidents, discovers YOUR causal graph (topology-constrained, stability-selected), scores every labeled incident into the calibration corpus, and refits the alarm-intensity model. Idempotent — run again whenever new data lands."),
    h("div", { style: "display:flex;gap:10px;margin-top:6px" },
      h("button", { class: "primary", onclick: async (ev) => {
        ev.target.disabled = true; ev.target.textContent = "Learning…";
        try {
          const res = await post(`/api/learn`);
          learnOut.textContent = JSON.stringify(res, null, 2);
          toast("Learning complete", "good");
          await refreshOverview();
        } catch (e) { toast(e.message, "crit"); }
        ev.target.disabled = false; ev.target.textContent = "Learn from connected data";
      } }, "Learn from connected data"),
      h("button", { onclick: () => { location.hash = "#/atlas"; } }, "Inspect the learned graph →")),
    learnOut));
}

/* ── AGENT GATEWAY (runtime trust layer for agentic AI) ─────────────────── */
async function viewGateway(root) {
  const [agents, decisions, approvals] = await Promise.all([
    fetch("/api/gateway/agents").then((r) => r.json()),
    fetch("/api/gateway/decisions?limit=40").then((r) => r.json()),
    fetch("/api/gateway/approvals").then((r) => r.json())]);
  const DCOL = { ALLOW: "good", BLOCK: "crit", ESCALATE: "warn",
                 SHADOW: "brass", ABSTAIN: "brass" };

  root.append(h("h1", { class: "page" }, "Agent Gateway"),
    h("div", { class: "page-sub" },
      "The runtime trust layer for ", h("b", {}, "any AI agent, any framework"),
      " — actions are checked before execution: tripwires always enforce, autonomy is earned per agent × action class from ",
      h("b", {}, "externally-verified outcomes"),
      ", and every decision is a signed certificate in the ",
      h("a", { href: "#/ledger", onclick: () => { DOM = "gateway"; localStorage.setItem("keel-domain", DOM); } }, "gateway ledger"),
      ". Shadow-first: observe and sign everything, block only catastrophes, earn trust with evidence."));

  root.append(h("div", { style: "margin-bottom:16px;display:flex;gap:10px" },
    h("button", { class: "primary", onclick: async () => {
      const pack = await api("/api/gateway/audit-pack?sample=25");
      const blob = new Blob([JSON.stringify(pack, null, 2)], { type: "application/json" });
      const a = h("a", { href: URL.createObjectURL(blob),
                         download: `keel-audit-pack-${Date.now()}.json` });
      a.click();
      if (pack.preview) {
        toast("Preview exported (3 decisions). Upgrade to Team for the full auditor-ready pack →", "");
        setTimeout(() => { location.hash = "#/billing"; }, 900);
      } else {
        toast(`Full audit pack exported: ${pack.sampled_decisions.length} verified decisions, chain ${pack.transparency_log.chain_consistent ? "consistent" : "BROKEN"}`, "good");
      }
    } }, "Export audit evidence pack"),
    h("span", { style: "font-size:11px;color:var(--ink-3);align-self:center" },
      "uniform-random sampled, signature-verified, Merkle-proven — the bundle ISO 42001 auditors and AI insurers request")));

  if (approvals.length) {
    root.append(h("div", { class: "panel", style: "margin-bottom:16px;border-color:rgba(176,110,16,.5)" },
      h("h3", {}, `Approval queue — ${approvals.length} waiting for a human`),
      ...approvals.map((d) => h("div", { class: "feed-item", style: "cursor:default" },
        h("span", { class: `verdict AMBIGUOUS` }, "ESCALATE"),
        h("div", { class: "t" },
          h("div", { class: "title" }, `${d.agent_id} · ${d.action_class}`),
          h("div", { class: "sub" }, d.reasons[0] || "")),
        h("button", { class: "primary", onclick: async () => {
          await post(`/api/gateway/approvals/${d.request_id}`, { allow: true, by: "operator" });
          toast("Approved — release certificate signed", "good"); route();
        } }, "Approve"),
        h("button", { class: "danger", onclick: async () => {
          await post(`/api/gateway/approvals/${d.request_id}`, { allow: false, by: "operator" });
          toast("Denied — recorded", "crit"); route();
        } }, "Deny")))));
  }

  root.append(h("div", { class: "grid", style: "grid-template-columns:1.15fr 1fr" },
    h("div", { class: "panel" },
      h("h3", {}, `Registered agents · ${agents.length}`),
      agents.length === 0 ? h("div", { class: "empty" },
        "No agents yet. Three lines of Python: from keel.sdk import KeelGuard → guard.register(…) → @guard.protect(…). Or run examples/gateway_quickstart.py.")
      : agents.map((a) => h("div", { style: "border:1px solid var(--hairline);border-radius:10px;padding:12px 14px;margin-bottom:10px" },
          h("div", { style: "display:flex;gap:10px;align-items:center;margin-bottom:8px" },
            h("b", {}, a.name),
            h("span", { class: "chip" }, a.framework || "custom"),
            a.shadow_mode ? h("span", { class: "chip brass" }, "shadow mode") : h("span", { class: "chip good" }, "enforcing"),
            h("span", { class: "src", style: "margin-left:auto;font:10px var(--mono);color:var(--ink-3)" }, a.agent_id)),
          ...Object.entries(a.calibration || {}).map(([cls, c]) =>
            h("div", { style: "display:flex;gap:8px;align-items:center;font-size:11.5px;padding:3px 0;border-top:1px solid var(--hairline)" },
              h("span", { class: "mono", style: "flex:1" }, cls),
              h("span", { class: `chip ${{ low: "", medium: "", high: "warn", critical: "crit" }[c.risk]}` }, c.risk),
              h("span", { class: "chip brass" }, `T${c.tier}`),
              h("span", { class: "mono", style: "color:var(--ink-3);font-size:10.5px" },
                c.confidence.n ? `p⩾${(c.confidence.p_lower ?? 0).toFixed(2)} · n=${c.confidence.n}` : "uncalibrated")))))),
    h("div", { class: "panel" },
      h("h3", {}, "Live decisions ", h("span", { class: "r" }, "signed · newest first")),
      decisions.length === 0 ? h("div", { class: "empty" }, "No decisions yet.")
      : decisions.slice(0, 22).map((d) => h("div", {
          class: "feed-item", onclick: () => {
            DOM = "gateway"; localStorage.setItem("keel-domain", DOM);
            location.hash = `#/cert/${d.cert_id}`;
          } },
          h("span", { class: `verdict ${{ ALLOW: "SUPPORTED", BLOCK: "REFUTED", ESCALATE: "AMBIGUOUS", SHADOW: "INSUFFICIENT", ABSTAIN: "ABSTAIN" }[d.decision]}` }, d.decision),
          h("div", { class: "t" },
            h("div", { class: "title", style: "font-size:12px" }, `${d.agent_id} · ${d.action_class}`),
            h("div", { class: "sub" }, (d.reasons[0] || "").slice(0, 84))),
          h("span", { class: "chip" }, d.risk))))));
}

/* ── BILLING / UPGRADE ($10 Team) ───────────────────────────────────────── */
const FEATURE_LABELS = {
  managed_hosting: "Managed, HA hosting",
  hsm_keys: "HSM-backed signing keys",
  approval_integrations: "Approval queue integrations (Slack, ticketing)",
  evidence_export_full: "Full evidence-pack export",
  evidence_scheduling: "Scheduled evidence packs",
  priority_support: "Priority email & chat support",
};

async function viewBilling(root) {
  const st = await api("/api/billing/status");
  const paid = st.valid && st.plan !== "free";
  // the billing period comes from the server so the price shown at the moment
  // of purchase can never disagree with what is actually charged
  const per = (st.price && st.price.period) || "week";
  const priceLabel = `${st.price_display}/${per}`;
  let me = null;
  try { me = await fetch("/api/auth/me").then((r) => r.json()); } catch { /* */ }
  if (me && me.email && me.email !== "default@local") {
    root.append(h("div", { class: "panel", style: "margin-bottom:16px" },
      h("h3", {}, "Account"),
      h("div", { style: "display:flex;gap:20px;flex-wrap:wrap;align-items:center;font-size:13px" },
        h("div", {}, h("span", { style: "color:var(--ink-3)" }, "signed in as "),
          h("b", {}, me.email)),
        h("div", {}, h("span", { style: "color:var(--ink-3)" }, "agent API key "),
          h("span", { class: "mono", style: "user-select:all;background:var(--panel-3);padding:3px 8px;border-radius:5px" },
            me.api_key || "—")),
        h("button", { class: "ghost", onclick: async () => {
          if (!confirm("Rotate the API key? Existing agents must update it.")) return;
          const r = await post("/api/auth/rotate-key"); toast("New key issued"); route(); } }, "Rotate"),
        h("button", { class: "ghost", onclick: async () => {
          await fetch("/api/auth/logout", { method: "POST" }); location.reload(); } }, "Sign out")),
      h("div", { style: "font-size:11.5px;color:var(--ink-3);margin-top:8px" },
        "Wire your agents with ", h("span", { class: "mono" }, "Authorization: Bearer <API key>"),
        " so their actions and billing attribute to this account.")));
  }
  root.append(h("h1", { class: "page" }, paid ? "Team plan — active" : "Upgrade to Team"),
    h("div", { class: "page-sub" },
      paid ? h("span", {}, "Your deployment is on the ", h("b", {}, "Team"),
        " plan. All features below are unlocked.")
           : h("span", {}, "Unlock managed hosting, hardened keys, approval-queue integrations, and the full compliance evidence workflow for ",
        h("b", {}, priceLabel), ".")));

  // plan card
  const feats = new Set(st.features);
  root.append(h("div", { class: "grid", style: "grid-template-columns:1.1fr 1fr;align-items:start" },
    h("div", { class: "panel" },
      h("h3", {}, "Team · ", h("span", { style: "color:var(--brass)" }, priceLabel),
        h("span", { class: "r" }, paid ? "active" : "not active")),
      h("div", {}, st.all_team_features.map((f) => {
        const on = feats.has(f);
        return h("div", { style: "display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--hairline);font-size:13.5px" },
          h("span", { style: `font-weight:700;color:${on ? "var(--good)" : "var(--ink-3)"}` }, on ? "✓" : "🔒"),
          h("span", { style: on ? "" : "color:var(--ink-3)" }, FEATURE_LABELS[f] || f));
      })),
      h("div", { style: "margin-top:16px;display:flex;gap:10px;flex-wrap:wrap" },
        paid
          ? h("button", { class: "danger", onclick: async () => {
              await post("/api/billing/deactivate"); toast("Downgraded to free"); route(); } },
              "Cancel Team")
          : h("button", { class: "primary", onclick: startCheckout },
              `Upgrade — ${priceLabel}`),
        !paid && !st.payments_live
          ? h("button", { onclick: devUnlock }, "Activate (dev / evaluation)")
          : null)),
    h("div", { class: "panel" },
      h("h3", {}, "How payment works"),
      h("div", { style: "font-size:13px;color:var(--ink-2);line-height:1.7" },
        st.payments_live
          ? h("span", {}, `Payments are live via ${st.provider === "razorpay" ? "Razorpay (UPI, cards, netbanking)" : "Stripe"}. Clicking Upgrade opens secure checkout for `,
              h("b", {}, st.price_display), ". On success you return here and Team unlocks automatically.")
          : h("span", {}, "No payment provider is configured on this deployment, so real payments are off. ",
              h("b", {}, "For evaluation"), ", use ",
              h("span", { class: "mono" }, "Activate (dev / evaluation)"),
              " to unlock Team locally. To take real payments in India set ",
              h("span", { class: "mono" }, "RAZORPAY_KEY_ID"), " + ",
              h("span", { class: "mono" }, "RAZORPAY_KEY_SECRET"),
              " (or Stripe elsewhere).")),
      st.valid ? h("div", { style: "margin-top:14px" },
        h("span", { class: "chip good" }, `plan ${st.plan}`), " ",
        st.expires_at ? h("span", { class: "chip" }, "renews " + new Date(st.expires_at * 1000).toISOString().slice(0, 10)) : null,
        st.source ? h("span", { class: "chip", style: "margin-left:6px" }, st.source) : null) : null)));

  // handle payment return (Stripe: session_id · Razorpay: razorpay_* params)
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  if (q.get("checkout") === "success") {
    const params = {}; q.forEach((v, k) => { params[k] = v; });
    const r = await post("/api/billing/confirm", params);
    toast(r.activated ? "Payment confirmed — Team unlocked" : "Could not confirm payment (" + (r.error || "") + ")", r.activated ? "good" : "crit");
    location.hash = "#/billing"; if (r.activated) setTimeout(route, 300);
  }

  async function startCheckout() {
    const r = await post("/api/billing/checkout", {});
    if ((r.mode === "razorpay" || r.mode === "stripe") && r.url) { location.href = r.url; }
    else if (r.mode === "dev") { toast(r.message, "crit"); }
    else { toast(r.error || "checkout unavailable", "crit"); }
  }
  async function devUnlock() {
    const code = prompt("Enter the deployment unlock code (dev/evaluation).\nDefault for local dev is: DEV-UNLOCK", "DEV-UNLOCK");
    if (code === null) return;
    try {
      const r = await post("/api/billing/activate", { code });
      toast("Team activated (evaluation) — features unlocked", "good");
      route();
    } catch (e) { toast("Activation failed: " + e.message, "crit"); }
  }
}

/* ── router ──────────────────────────────────────────────────────────────── */
async function route() {
  const hash = location.hash || (DOM ? "#/deck" : "#/gateway");
  const [, view, arg] = hash.split("/");
  const root = $("#view"); root.innerHTML = ""; hideTip();
  renderNav(view);
  const needsDomain = ["deck", "incident", "certs", "cert", "atlas",
                       "calibration", "ledger", "evidence", "policy"];
  if (needsDomain.includes(view) && !DOM) {
    root.append(h("div", { class: "panel", style: "max-width:640px;margin:60px auto;text-align:center" },
      h("h3", {}, "No data connected yet — and that is by design"),
      h("div", { style: "font-size:13px;color:var(--ink-2);line-height:1.7;margin-bottom:16px" },
        "KEEL ships with zero mock data. Put your AI agents behind the trust gateway, or connect your operational data for causal verification."),
      h("div", { style: "display:flex;gap:10px;justify-content:center" },
        h("button", { class: "primary", onclick: () => location.hash = "#/gateway" }, "Agent Gateway →"),
        h("button", { onclick: () => location.hash = "#/connect" }, "Connect your data →"))));
    return;
  }
  try {
    if (view === "deck") await viewDeck(root);
    else if (view === "incident") await viewIncident(root, arg);
    else if (view === "certs") await viewCerts(root);
    else if (view === "cert") await viewCert(root, arg);
    else if (view === "atlas") await viewAtlas(root);
    else if (view === "calibration") await viewCalibration(root);
    else if (view === "ledger") await viewLedger(root);
    else if (view === "evidence") await viewEvidence(root);
    else if (view === "policy") await viewPolicy(root);
    else if (view === "connect") await viewConnect(root);
    else if (view === "gateway") await viewGateway(root);
    else if (view === "billing") await viewBilling(root);
    else { location.hash = "#/deck"; }
  } catch (e) {
    root.append(h("div", { class: "empty" }, `Failed to load: ${e.message}. The engine may still be seeding — retrying in 3s.`));
    setTimeout(route, 3000);
  }
}
addEventListener("hashchange", route);

// ── auth gate ────────────────────────────────────────────────────────────
let ACCOUNT = null;

function authScreen(mode) {
  document.body.innerHTML = "";
  const wrap = h("div", { style: "min-height:100vh;display:grid;place-items:center;background:var(--abyss)" });
  const card = h("div", { class: "panel", style: "width:380px;max-width:92vw;padding:32px" });
  const email = h("input", { class: "ing-line", style: "width:100%;margin-bottom:10px", type: "email", placeholder: "you@company.com" });
  const pw = h("input", { class: "ing-line", style: "width:100%;margin-bottom:6px", type: "password", placeholder: "password (min 8 chars)" });
  const name = h("input", { class: "ing-line", style: "width:100%;margin-bottom:10px", placeholder: "your name (optional)" });
  const err = h("div", { style: "color:var(--crit);font-size:12px;min-height:16px;margin-bottom:10px" });
  const submit = async () => {
    err.textContent = "";
    try {
      const path = mode === "signup" ? "/api/auth/signup" : "/api/auth/login";
      const body = mode === "signup"
        ? { email: email.value, password: pw.value, name: name.value }
        : { email: email.value, password: pw.value };
      const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) { err.textContent = (await r.json()).detail || "failed"; return; }
      location.reload();
    } catch (e) { err.textContent = String(e.message || e); }
  };
  [
    h("div", { class: "wordmark", style: "padding:0 0 18px" },
      h("div", { class: "name", style: "font-size:30px" }, "KE", h("b", {}, "E"), "L"),
      h("div", { class: "sub" }, "Causal Verification Authority")),
    h("h3", { style: "font-size:15px;margin-bottom:16px;color:var(--ink)" },
      mode === "signup" ? "Create your account" : "Sign in"),
    email, pw, mode === "signup" ? name : null, err,
    h("button", { class: "primary", style: "width:100%", onclick: submit },
      mode === "signup" ? "Create account & sign in" : "Sign in"),
    h("div", { style: "text-align:center;margin-top:14px;font-size:12.5px;color:var(--ink-3)" },
      mode === "signup" ? "Already have an account? " : "New to KEEL? ",
      h("a", { href: "#", style: "color:var(--brass)", onclick: (e) => { e.preventDefault();
        authScreen(mode === "signup" ? "login" : "signup"); } },
        mode === "signup" ? "Sign in" : "Create one")),
  ].filter(Boolean).forEach((k) => card.append(k));
  [email, pw, name].forEach((i) => i && i.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); }));
  wrap.append(card); document.body.append(wrap);
}

async function boot() {
  try {
    const cfg = await fetch("/api/auth/config").then((r) => r.json());
    const me = await fetch("/api/auth/me");
    if (me.status === 401) { authScreen(cfg.has_accounts ? "login" : "signup"); return; }
    ACCOUNT = await me.json();
  } catch { /* server booting — proceed, endpoints will retry */ }
  await refreshOverview();
  route();
}
boot();
