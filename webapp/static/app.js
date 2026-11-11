"use strict";
// Off-peak front end: advice, region, notifications and the solar calculator.

const $ = (sel) => document.querySelector(sel);
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};
const state = {
  region: store.get("region", "C"),
  shift: Number(store.get("shift", "5")) || 5,
  times: JSON.parse(store.get("times", '["08:00"]')),
  config: null,
};
const gbp = (v, dp = 2) => v.toLocaleString("en-GB", { style: "currency", currency: "GBP", minimumFractionDigits: dp, maximumFractionDigits: dp });
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "Check the values you entered.";
    throw new Error(detail);
  }
  return body;
}

function setMsg(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle("err", isError);
}

/* ---------- tabs ---------- */
function showTab(name) {
  document.querySelectorAll(".tab[data-tab]").forEach((b) => {
    if (b.dataset.tab === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  $("#today").hidden = name !== "today";
  $("#solar").hidden = name !== "solar";
  history.replaceState(null, "", name === "solar" ? "#solar" : location.pathname);
  if (name === "solar" && !$("#solar-results").children.length) runSolar();
  else if (name === "solar" && lastMonths) renderMonths(lastMonths);
  if (name === "today" && lastSlots) renderRibbon(lastSlots);
}
document.querySelectorAll(".tab[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------- regions ---------- */
function fillRegions(regions) {
  for (const sel of [$("#region"), $("#solar-region")]) {
    sel.innerHTML = "";
    for (const [code, name] of Object.entries(regions)) {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = `${name} (${code})`;
      sel.append(opt);
    }
    sel.value = state.region;
  }
}

function setRegion(code) {
  state.region = code;
  store.set("region", code);
  $("#region").value = code;
  $("#solar-region").value = code;
  loadAdvice();
}

$("#region").addEventListener("change", (e) => {
  setRegion(e.target.value);
  setMsg($("#region-msg"), `Region set to ${state.config.regions[e.target.value]}.`);
});

$("#postcode-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pc = $("#postcode").value.trim();
  if (!pc) return setMsg($("#region-msg"), "Enter a postcode first.", true);
  try {
    const r = await api(`/api/region?postcode=${encodeURIComponent(pc)}`);
    setRegion(r.region);
    setMsg($("#region-msg"), `Region set to ${r.name}.`);
  } catch (err) {
    setMsg($("#region-msg"), err.message, true);
  }
});

/* ---------- advice ---------- */
async function loadAdvice() {
  document.body.classList.add("loading");
  try {
    const a = await api(`/api/advice?region=${state.region}&shift_kwh=${state.shift}`);
    renderAdvice(a);
  } catch (err) {
    $("#hero-lead").textContent = "Prices aren’t available right now.";
    $("#hero-time").textContent = "–";
    $("#hero-sub").textContent = `${err.message} Try again in a few minutes.`;
  } finally {
    document.body.classList.remove("loading");
  }
}

function renderAdvice(a) {
  const first = a.cheapest[0];
  $("#hero-lead").textContent = `Cheapest time in ${a.region_name} in the next 24 hours`;
  $("#hero-time").textContent = `${first.start}–${first.end}`;
  $("#hero-sub").textContent = `${first.avg_p.toFixed(1)}p per kWh, against an average of ${a.average_p.toFixed(1)}p.`;

  const ul = $("#cheapest");
  ul.innerHTML = "";
  for (const c of a.cheapest) {
    const li = document.createElement("li");
    const when = document.createElement("strong");
    when.textContent = `${c.start}–${c.end}`;
    const price = document.createElement("span");
    price.textContent = `${c.avg_p.toFixed(1)}p`;
    li.append(when, price);
    ul.append(li);
  }
  const avoid = a.avoid[0];
  $("#avoid").textContent = avoid ? `Avoid ${avoid.start}–${avoid.end}: ${avoid.avg_p.toFixed(1)}p per kWh on average.` : "";
  const neg = $("#negative");
  neg.hidden = !a.negative.length;
  neg.textContent = a.negative.length
    ? `Negative prices ${a.negative.map((n) => `${n.start}–${n.end}`).join(", ")}: you’re paid to use electricity. ` : "";
  if (a.negative.length) {
    const link = document.createElement("a");
    link.href = "/guides/negative-prices";
    link.textContent = "How to make the most of it";
    neg.append(link);
  }

  const vsPeak = a.saving_vs_peak_gbp;
  const saving = $("#saving");
  saving.innerHTML = "";
  const amount = document.createElement("strong");
  amount.textContent = gbp(vsPeak ?? a.saving_vs_average_gbp);
  saving.append(amount, document.createTextNode(
    ` today by moving ${a.shift_kwh} kWh into the cheapest hours instead of ${vsPeak != null ? "the evening peak" : "using it at the average price"}. ` +
    `Compared with the average price, that’s about ${gbp(a.monthly_vs_average_gbp, 0)} a month.`));
  $("#tip").textContent = a.tip;
  renderRibbon(a.slots);
}

function svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

let lastSlots = null, lastMonths = null;
function chartWidth(box) { return Math.max(300, Math.round(box.clientWidth || 720)); }

function renderRibbon(slots) {
  lastSlots = slots;
  const box = $("#ribbon");
  const W = chartWidth(box), H = 190, padB = 22, padT = 8;
  const prices = slots.map((s) => s.price);
  const lo = Math.min(0, ...prices), hi = Math.max(1, ...prices);
  const y = (p) => padT + (hi - p) / (hi - lo) * (H - padT - padB);
  const bw = W / slots.length;
  const style = getComputedStyle(document.documentElement);
  const colour = (name) => style.getPropertyValue(name).trim();
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H });
  const every = W < 500 ? 6 : 3;  // hours between axis labels
  slots.forEach((s, i) => {
    const label = s.start.slice(11, 16);
    const hour = Number(label.slice(0, 2));
    const fill = s.best ? colour("--cheap") : (hour >= 16 && hour < 19 ? colour("--peak") : colour("--mid"));
    const top = Math.min(y(s.price), y(0)), h = Math.max(1.5, Math.abs(y(s.price) - y(0)));
    const bar = svgEl("rect", { x: i * bw + 0.5, y: top, width: Math.max(1, bw - 1.5), height: h, fill, rx: 1.5 });
    const tip = svgEl("title", {});
    tip.textContent = `${label}: ${s.price.toFixed(1)}p per kWh`;
    bar.append(tip);
    svg.append(bar);
    if (label.endsWith(":00") && hour % every === 0) {
      const t = svgEl("text", { x: i * bw, y: H - 6, "font-size": 11, fill: colour("--muted") });
      t.textContent = label;
      svg.append(t);
    }
  });
  svg.append(svgEl("line", { x1: 0, x2: W, y1: y(0), y2: y(0), stroke: colour("--line"), "stroke-width": 1 }));
  box.innerHTML = "";
  box.append(svg);
}

$("#shift").value = state.shift;
$("#shift").addEventListener("input", debounce((e) => {
  const v = Number(e.target.value);
  if (v >= 0.5 && v <= 100) { state.shift = v; store.set("shift", String(v)); loadAdvice(); }
}, 400));

/* ---------- notifications ---------- */
const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

function halfHours() {
  const out = [];
  for (let h = 0; h < 24; h++) for (const m of ["00", "30"]) out.push(`${String(h).padStart(2, "0")}:${m}`);
  return out;
}

function renderPickers() {
  const box = $("#time-pickers");
  box.innerHTML = "";
  state.times.forEach((t, i) => {
    const wrap = document.createElement("div");
    wrap.className = "picker";
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", `Notification time ${i + 1}`);
    for (const opt of halfHours()) sel.append(new Option(opt, opt, false, opt === t));
    sel.addEventListener("change", () => { state.times[i] = sel.value; store.set("times", JSON.stringify(state.times)); });
    wrap.append(sel);
    if (state.times.length > 1) {
      const rm = document.createElement("button");
      rm.type = "button"; rm.className = "remove"; rm.textContent = "Remove";
      rm.setAttribute("aria-label", `Remove time ${t}`);
      rm.addEventListener("click", () => { state.times.splice(i, 1); store.set("times", JSON.stringify(state.times)); renderPickers(); });
      wrap.append(rm);
    }
    box.append(wrap);
  });
  $("#add-time").hidden = state.times.length >= 3;
}
$("#add-time").addEventListener("click", () => {
  const defaults = ["08:00", "16:30", "21:00"];
  state.times.push(defaults.find((d) => !state.times.includes(d)) || "12:00");
  store.set("times", JSON.stringify(state.times));
  renderPickers();
});

function urlB64ToUint8Array(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

async function currentSubscription() {
  if (!pushSupported) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

function showSubscribed(on) {
  $("#notify-on").textContent = on ? "Update notifications" : "Turn on notifications";
  $("#notify-test").hidden = !on;
  $("#notify-off").hidden = !on;
}

$("#notify-on").addEventListener("click", async () => {
  const msg = $("#notify-msg");
  if (!pushSupported) return setMsg(msg, isIOS ? "Add this page to your Home Screen first, then open it from there." : "This browser doesn’t support notifications. Try Chrome, Edge or Firefox.", true);
  if (!state.config.push_enabled) return setMsg(msg, "Notifications aren’t set up on this server yet.", true);
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return setMsg(msg, "Notifications are blocked. Allow them for this site in your browser settings, then try again.", true);
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription()) ||
      (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(state.config.vapid_public_key) }));
    const r = await api("/api/subscribe", {
      method: "POST",
      body: JSON.stringify({ subscription: sub.toJSON(), region: state.region, times: state.times, shift_kwh: state.shift }),
    });
    showSubscribed(true);
    setMsg(msg, `Notifications on at ${r.times.join(", ")}.`);
  } catch (err) {
    setMsg(msg, `Couldn’t turn on notifications: ${err.message}`, true);
  }
});

$("#notify-test").addEventListener("click", async () => {
  const msg = $("#notify-msg");
  try {
    const sub = await currentSubscription();
    if (!sub) { showSubscribed(false); return setMsg(msg, "Notifications are off in this browser. Turn them on first.", true); }
    await api("/api/test-push", { method: "POST", body: JSON.stringify({ endpoint: sub.endpoint }) });
    setMsg(msg, "Test notification sent. It should arrive within a minute.");
  } catch (err) {
    setMsg(msg, err.message, true);
  }
});

$("#notify-off").addEventListener("click", async () => {
  const msg = $("#notify-msg");
  try {
    const sub = await currentSubscription();
    if (sub) {
      await api("/api/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: sub.endpoint }) });
      await sub.unsubscribe();
    }
    showSubscribed(false);
    setMsg(msg, "Notifications turned off.");
  } catch (err) {
    setMsg(msg, err.message, true);
  }
});

/* ---------- solar calculator ---------- */
function solarInputs() {
  const data = {};
  for (const el of $("#solar-form").elements) {
    if (!el.name) continue;
    data[el.name] = el.name === "region" ? el.value : Number(el.value);
  }
  return data;
}

async function runSolar() {
  const msg = $("#solar-msg");
  try {
    const r = await api("/api/solar", { method: "POST", body: JSON.stringify(solarInputs()) });
    setMsg(msg, "");
    $("#solar-headline").textContent = r.payback_years != null ? `Pays back in ${r.payback_years} years` : "Doesn’t pay back within 20 years";
    $("#solar-sub").textContent = `Saves about ${gbp(r.annual_saving_gbp, 0)} a year on an upfront cost of ${gbp(r.upfront_cost_gbp, 0)}.`;
    const figures = [
      ["Saving a year", gbp(r.annual_saving_gbp, 0)],
      ["Upfront cost", gbp(r.upfront_cost_gbp, 0)],
      ["Net gain over 20 years", gbp(r.net_saving_20y_gbp, 0)],
      ["Solar used at home", `${r.self_use_pct}%`],
      ["Solar generated", `${r.generation_kwh.toLocaleString("en-GB")} kWh`],
      ["Exported", `${r.exported_kwh.toLocaleString("en-GB")} kWh`],
      ["Bill before", gbp(r.bill_before_gbp, 0)],
      ["Bill after", gbp(r.bill_after_gbp, 0)],
    ];
    const dl = $("#solar-results");
    dl.innerHTML = "";
    for (const [label, value] of figures) {
      const div = document.createElement("div");
      const dt = document.createElement("dt"); dt.textContent = label;
      const dd = document.createElement("dd"); dd.textContent = value;
      div.append(dt, dd);
      dl.append(div);
    }
    renderMonths(r.monthly);
    const ul = $("#assumptions");
    ul.innerHTML = "";
    for (const a of r.assumptions) { const li = document.createElement("li"); li.textContent = a; ul.append(li); }
  } catch (err) {
    setMsg(msg, err.message, true);
  }
}

function renderMonths(months) {
  lastMonths = months;
  const box = $("#months");
  const W = chartWidth(box), H = 170, padB = 22;
  const hi = Math.max(1, ...months.map((m) => m.saving_gbp));
  const bw = W / months.length;
  const style = getComputedStyle(document.documentElement);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H });
  const gap = Math.min(12, bw * 0.25);
  months.forEach((m, i) => {
    const h = Math.max(1, (Math.max(0, m.saving_gbp) / hi) * (H - padB - 18));
    const bar = svgEl("rect", { x: i * bw + gap / 2, y: H - padB - h, width: bw - gap, height: h, fill: style.getPropertyValue("--cheap").trim(), rx: 3 });
    const tip = svgEl("title", {}); tip.textContent = `${m.month}: ${gbp(m.saving_gbp)} saved, ${m.generation_kwh} kWh generated`;
    bar.append(tip);
    const label = svgEl("text", { x: i * bw + bw / 2, y: H - 6, "font-size": 12, "text-anchor": "middle", fill: style.getPropertyValue("--muted").trim() });
    label.textContent = m.month;
    const value = svgEl("text", { x: i * bw + bw / 2, y: H - padB - h - 4, "font-size": 11, "text-anchor": "middle", fill: style.getPropertyValue("--ink").trim() });
    value.textContent = `£${Math.round(m.saving_gbp)}`;
    svg.append(bar, label, value);
  });
  box.innerHTML = "";
  box.append(svg);
}
$("#solar-form").addEventListener("input", debounce(runSolar, 350));
window.addEventListener("resize", debounce(() => {
  if (lastSlots && !$("#today").hidden) renderRibbon(lastSlots);
  if (lastMonths && !$("#solar").hidden) renderMonths(lastMonths);
}, 200));

/* ---------- start ---------- */
(async function init() {
  if (isIOS && !standalone) $("#ios-note").hidden = false;
  renderPickers();
  try {
    state.config = await api("/api/config");
    if (!state.config.regions[state.region]) state.region = "C";
    fillRegions(state.config.regions);
  } catch (err) {
    $("#hero-lead").textContent = "The app couldn’t reach its server. Check your connection and reload.";
    return;
  }
  loadAdvice();
  if (pushSupported) {
    try {
      await navigator.serviceWorker.register("/sw.js");
      showSubscribed(Boolean(await currentSubscription()));
    } catch (err) { /* notifications stay off */ }
  }
  if (location.hash === "#solar") showTab("solar");
})();
