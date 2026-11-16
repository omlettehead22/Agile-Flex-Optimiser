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
window.addEventListener("hashchange", () => showTab(location.hash === "#solar" ? "solar" : "today"));

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

// "tomorrow 16:30–17:30" for ranges that start tomorrow, so they aren't read as already past.
const span = (b) => `${b.start}–${b.end}`;
const when = (b) => (b.day === "tomorrow" ? `tomorrow ${span(b)}` : span(b));
const When = (b) => (b.day === "tomorrow" ? `Tomorrow ${span(b)}` : span(b));

function renderAdvice(a) {
  const first = a.cheapest[0];
  const below = (p) => Math.round((1 - p / a.average_p) * 100);
  $("#hero-lead").textContent = `Cheapest time in ${a.region_name}, next 24 hours`;
  $("#hero-time").textContent = span(first);
  const sub = $("#hero-sub");
  sub.textContent = `${first.day === "tomorrow" ? "Tomorrow, at " : "At "}${first.avg_p.toFixed(1)}p per kWh. `;
  if (a.average_p > 0 && below(first.avg_p) > 0) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = `${below(first.avg_p)}% below the day’s average`;
    sub.append(badge, document.createTextNode(` of ${a.average_p.toFixed(1)}p.`));
  } else {
    sub.append(document.createTextNode(`The day’s average is ${a.average_p.toFixed(1)}p.`));
  }

  const ul = $("#cheapest");
  ul.innerHTML = "";
  for (const c of a.cheapest) {
    const li = document.createElement("li");
    const whenEl = document.createElement("strong");
    whenEl.textContent = When(c);
    const price = document.createElement("span");
    price.textContent = `${c.avg_p.toFixed(1)}p`;
    li.append(whenEl, price);
    ul.append(li);
  }
  const avoid = a.avoid[0];
  $("#avoid").textContent = avoid ? `Avoid ${when(avoid)}: ${avoid.avg_p.toFixed(1)}p per kWh on average.` : "";
  const neg = $("#negative");
  neg.hidden = !a.negative.length;
  neg.textContent = a.negative.length
    ? `Negative prices ${a.negative.map(when).join(", ")}: you’re paid to use electricity. ` : "";
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
    `today, by moving ${a.shift_kwh} kWh into the cheapest hours instead of ${vsPeak != null ? "the evening peak" : "paying the average price"}. ` +
    `Against the average price, that’s about ${gbp(a.monthly_vs_average_gbp, 0)} a month.`));
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
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const niceStep = (range) => (range > 80 ? 20 : range > 30 ? 10 : 5);

function runs(flags) {  // [[startIndex, length], ...] for consecutive true values
  const out = [];
  flags.forEach((f, i) => {
    if (!f) return;
    if (out.length && out[out.length - 1][0] + out[out.length - 1][1] === i) out[out.length - 1][1] += 1;
    else out.push([i, 1]);
  });
  return out;
}

function setReadout(slots, i) {
  const r = $("#readout");
  const s = slots[i];
  const end = slots[i + 1] ? slots[i + 1].start.slice(11, 16) : "";
  const day = s.start.slice(0, 10) !== slots[0].start.slice(0, 10) ? "tomorrow " : "";
  r.innerHTML = "";
  const p = document.createElement("span"); p.className = "p"; p.textContent = `${s.price.toFixed(1)}p`;
  const t = document.createElement("span"); t.className = "t";
  t.textContent = i === 0 ? ` per kWh now, until ${end}` : ` per kWh, ${day}${s.start.slice(11, 16)}–${end}`;
  r.append(p, t);
}

function renderRibbon(slots) {
  lastSlots = slots;
  const box = $("#ribbon");
  const W = chartWidth(box), narrow = W < 520, H = narrow ? 210 : 250;
  const padL = 34, padR = 2, padT = 20, padB = 24;
  const prices = slots.map((s) => s.price);
  const step = niceStep(Math.max(...prices) - Math.min(0, ...prices));
  const lo = Math.floor(Math.min(0, ...prices) / step) * step, hi = Math.ceil(Math.max(1, ...prices) / step) * step;
  const y = (p) => padT + (hi - p) / (hi - lo) * (H - padT - padB);
  const bw = (W - padL - padR) / slots.length;
  const x = (i) => padL + i * bw;
  const c = { cheap: cssVar("--cheap"), mid: cssVar("--mid"), peak: cssVar("--peak"), muted: cssVar("--muted"),
              line: cssVar("--line"), ink: cssVar("--ink"), cheapWash: cssVar("--cheap-wash"), peakWash: cssVar("--peak-wash") };
  const hourOf = (s) => Number(s.start.slice(11, 13));
  const isPeak = slots.map((s) => hourOf(s) >= 16 && hourOf(s) < 19);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, tabindex: 0,
                             "aria-label": "Price chart. Use the left and right arrow keys to read each half-hour." });

  for (let v = lo; v <= hi; v += step) {  // price gridlines
    svg.append(svgEl("line", { x1: padL, x2: W - padR, y1: y(v), y2: y(v), stroke: c.line, "stroke-width": v === 0 ? 1.2 : 1,
                               "stroke-dasharray": v === 0 ? "" : "2 4" }));
    const t = svgEl("text", { x: padL - 6, y: y(v) + 4, "font-size": 11, "text-anchor": "end", fill: c.muted });
    t.textContent = `${v}p`;
    svg.append(t);
  }
  for (const [i, n] of runs(isPeak)) {
    svg.append(svgEl("rect", { x: x(i), y: padT, width: n * bw, height: H - padT - padB, fill: c.peakWash }));
  }
  for (const [i, n] of runs(slots.map((s) => s.best))) {
    svg.append(svgEl("rect", { x: x(i), y: padT, width: n * bw, height: H - padT - padB, fill: c.cheapWash, rx: 4 }));
  }

  const gap = bw > 10 ? 2 : 1;
  slots.forEach((s, i) => {
    const fill = s.best ? c.cheap : isPeak[i] ? c.peak : c.mid;
    const top = Math.min(y(s.price), y(0)), h = Math.max(2, Math.abs(y(s.price) - y(0)));
    svg.append(svgEl("rect", { x: x(i) + gap / 2, y: top, width: Math.max(1, bw - gap), height: h, fill, rx: Math.min(3, bw / 4) }));
    const label = s.start.slice(11, 16), hour = hourOf(s);
    if (label.endsWith(":00") && hour % (narrow ? 6 : 3) === 0 && i > 1) {
      const t = svgEl("text", { x: x(i), y: H - 6, "font-size": 11, "text-anchor": "middle", fill: c.muted });
      t.textContent = label;
      svg.append(t);
    }
    if (i > 0 && s.start.slice(0, 10) !== slots[i - 1].start.slice(0, 10)) {  // midnight
      svg.append(svgEl("line", { x1: x(i), x2: x(i), y1: 4, y2: H - padB, stroke: c.muted, "stroke-width": 1, "stroke-dasharray": "3 3" }));
      const t = svgEl("text", { x: x(i) + 5, y: 13, "font-size": 11, fill: c.muted });
      t.textContent = "Tomorrow";
      svg.append(t);
    }
  });
  const now = svgEl("text", { x: padL, y: 13, "font-size": 11, "font-weight": 600, fill: c.ink });
  now.textContent = "Now";
  svg.append(now);

  const cursor = svgEl("rect", { x: 0, y: padT - 4, width: bw, height: H - padT - padB + 4, fill: "none",
                                 stroke: c.ink, "stroke-width": 1.5, rx: 4, visibility: "hidden" });
  svg.append(cursor);
  let current = 0;
  const show = (i) => {
    current = Math.max(0, Math.min(slots.length - 1, i));
    cursor.setAttribute("x", x(current));
    cursor.setAttribute("visibility", "visible");
    setReadout(slots, current);
  };
  const reset = () => { cursor.setAttribute("visibility", "hidden"); setReadout(slots, 0); };
  const indexAt = (ev) => {
    const r = svg.getBoundingClientRect();
    return Math.floor(((ev.clientX - r.left) * (W / r.width) - padL) / bw);
  };
  svg.addEventListener("pointermove", (ev) => show(indexAt(ev)));
  svg.addEventListener("pointerdown", (ev) => show(indexAt(ev)));
  svg.addEventListener("pointerleave", reset);
  svg.addEventListener("blur", reset);
  svg.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
      ev.preventDefault();
      show(cursor.getAttribute("visibility") === "hidden" ? 0 : current + (ev.key === "ArrowRight" ? 1 : -1));
    }
  });

  box.innerHTML = "";
  box.append(svg);
  setReadout(slots, 0);
}

$("#shift").value = state.shift;
document.querySelectorAll(".step").forEach((btn) => btn.addEventListener("click", () => {
  const input = $("#shift");
  const v = Math.min(100, Math.max(0.5, (Number(input.value) || 0) + Number(btn.dataset.step)));
  input.value = v;
  input.dispatchEvent(new Event("input"));
}));
$("#change-region").addEventListener("click", (e) => {
  e.preventDefault();
  $("#where-h").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  $("#postcode").focus({ preventScroll: true });
});
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
      if (label === "Saving a year") div.className = "key";
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
  const W = chartWidth(box), H = 180, padB = 22, padT = 18;
  const hi = Math.max(1, ...months.map((m) => m.saving_gbp));
  const bw = W / months.length;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H });
  const gap = Math.min(10, bw * 0.28);
  const best = months.reduce((b, m, i) => (m.saving_gbp > months[b].saving_gbp ? i : b), 0);
  months.forEach((m, i) => {
    const h = Math.max(2, (Math.max(0, m.saving_gbp) / hi) * (H - padB - padT));
    const bar = svgEl("rect", { x: i * bw + gap / 2, y: H - padB - h, width: bw - gap, height: h,
                                fill: cssVar("--cheap"), rx: Math.min(5, (bw - gap) / 3) });
    const tip = svgEl("title", {}); tip.textContent = `${m.month}: ${gbp(m.saving_gbp)} saved, ${m.generation_kwh} kWh generated`;
    bar.append(tip);
    const label = svgEl("text", { x: i * bw + bw / 2, y: H - 6, "font-size": 11, "text-anchor": "middle", fill: cssVar("--muted") });
    label.textContent = W < 520 ? m.month.slice(0, 1) : m.month;
    svg.append(bar, label);
    if (i === best || bw > 44) {
      const value = svgEl("text", { x: i * bw + bw / 2, y: H - padB - h - 5, "font-size": 11, "text-anchor": "middle",
                                    fill: cssVar("--ink"), "font-weight": i === best ? 600 : 400 });
      value.textContent = `£${Math.round(m.saving_gbp)}`;
      svg.append(value);
    }
  });
  box.innerHTML = "";
  box.append(svg);
}
$("#solar-form").addEventListener("input", debounce(runSolar, 350));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (lastSlots) renderRibbon(lastSlots);
  if (lastMonths) renderMonths(lastMonths);
});
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
