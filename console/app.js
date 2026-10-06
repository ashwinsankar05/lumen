// LUMEN operator console. Plain JS, no dependencies: it must run with the network unplugged.
"use strict";

const $ = (sel) => document.querySelector(sel);
const WINDOW_S = 10;     // seconds of trace on screen
const WARMUP_S = 8;      // grey warm-up before the probe (SessionConfig.warmup_s)
const state = { sessionId: null, next: 0, samples: [], timer: null, thresholds: null };

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

const fmt = (v, d = 3) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const signed = (v, d = 3) => (v === null || v === undefined ? "–" : (v >= 0 ? "+" : "") + Number(v).toFixed(d));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const clamp01 = (x) => Math.max(0, Math.min(1, x));

// ---------------------------------------------------------------- theme
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("lumen-theme", theme); } catch (e) { /* storage may be blocked */ }
  drawTraces();
}
$("#theme-btn").addEventListener("click", () =>
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
try { const saved = localStorage.getItem("lumen-theme"); if (saved) document.documentElement.dataset.theme = saved; } catch (e) { /* ignore */ }

// ---------------------------------------------------------------- health + calibration status
async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("#pill-key").textContent = `key ${h.public_key.slice(0, 10)}…`;
  } catch (e) {
    $("#pill-key").textContent = "service unreachable";
  }
}

async function loadThresholds() {
  try {
    state.thresholds = await api("/api/thresholds");
  } catch (e) {
    return;
  }
  const th = state.thresholds;
  const pill = $("#pill-cal");
  if (th.provisional) {
    pill.className = "pill warn";
    pill.textContent = "Not calibrated: verdicts provisional";
  } else {
    pill.className = "pill good";
    pill.textContent = `Calibrated · ${th.n_genuine} genuine sessions`;
  }
  const l3 = th.cheek_contrast_min === null ? "3D check (L3) not calibrated yet" : `3D check needs cheek contrast ≥ ${th.cheek_contrast_min}`;
  $("#thresholds").textContent = `Thresholds: ${th.source}. ${l3}.`;
}

// ---------------------------------------------------------------- start form
const preset = $("#source-preset");
preset.addEventListener("change", () => { $("#custom-source-field").hidden = preset.value !== "other"; });
$("#duration").addEventListener("input", (e) => { $("#duration-out").textContent = `${e.target.value} s`; });
$("#amplitude").addEventListener("input", (e) => { $("#amp-out").textContent = e.target.value; });

$("#start-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const source = preset.value === "other" ? $("#source-custom").value.trim() : preset.value;
  if (!source) { alert("Enter a source, e.g. file:… or screen:x,y,w,h"); return; }
  const body = {
    source,
    layout: f.get("layout"),
    emit: f.get("emit"),
    duration_s: Number(f.get("duration_s")),
    amplitude: Number(f.get("amplitude")),
    label: f.get("label") || "",
    fullscreen: f.get("fullscreen") === "on",
    save_video: f.get("save_video") === "on",
  };
  if (body.emit === "virtualcam") body.max_lag_s = 2.0; // a call adds network and buffering delay
  try {
    const { session_id } = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    Object.assign(state, { sessionId: session_id, next: 0, samples: [] });
    $("#start-btn").disabled = true;
    $("#stop-btn").disabled = false;
    resetResult();
    poll();
  } catch (e) {
    alert(`Could not start: ${e.message}`);
  }
});

$("#stop-btn").addEventListener("click", async () => {
  if (state.sessionId) await api(`/api/sessions/${state.sessionId}/stop`, { method: "POST" }).catch(() => {});
});

// ---------------------------------------------------------------- steps + hero
const PHASES = ["warmup", "probe", "analysis", "verdict"];
function setStep(phase) {
  const i = PHASES.indexOf(phase);
  document.querySelectorAll("#steps li").forEach((li, k) => {
    li.classList.toggle("done", i >= 0 && k < i || phase === "verdict" && k === i);
    li.classList.toggle("active", k === i && phase !== "verdict");
  });
}

function setHero(kind, word, reason, badges = []) {
  $("#verdict").className = `verdict ${kind}`;
  $("#verdict-icon").textContent = { verify: "✓", reject: "✕", abstain: "?", running: "◌", idle: "◎" }[kind] || "◎";
  $("#verdict-word").textContent = word;
  $("#verdict-reason").textContent = reason;
  $("#verdict-badges").innerHTML = badges.map(([cls, txt]) => `<span class="badge ${cls}">${esc(txt)}</span>`).join("");
}

const PHASE_TEXT = {
  warmup: "Grey screen: letting the camera settle before the light pattern starts.",
  probe: "Showing a one-time colour pattern and watching how the face, neck and cheeks respond.",
  analysis: "Running the physics checks…",
};

// ---------------------------------------------------------------- layer cards
function layerCard(id, { status, value, sub, meter, mark, cheeks }) {
  const card = $(`#layer-${id}`);
  card.classList.remove("pass", "fail", "live");
  if (status === "pass" || status === "fail" || status === "live") card.classList.add(status);
  const st = card.querySelector(".state");
  st.dataset.state = status;
  st.textContent = { pass: "PASS", fail: "FAIL", live: "LIVE", idle: "—", skip: "NOT REACHED" }[status] || "—";
  card.querySelector('[data-k="value"]').textContent = value ?? "—";
  if (sub !== undefined) card.querySelector('[data-k="sub"]').textContent = sub;
  const fill = card.querySelector(".meter .fill");
  const mk = card.querySelector(".meter .mark");
  if (fill) fill.style.width = `${100 * clamp01(meter ?? 0)}%`;
  if (mk) { mk.classList.toggle("on", mark !== undefined && mark !== null); mk.style.left = `${100 * clamp01(mark ?? 0)}%`; }
  if (cheeks && id === "L3") drawCheeks(cheeks.left, cheeks.right);
}

function drawCheeks(left, right) {
  // Lateral index in [-1, 1]: negative leans to the screen's right half, positive to the left.
  const set = (sel, v) => {
    const r = $(`#layer-L3 .bar.${sel}`);
    const w = 150 * Math.min(1, Math.abs(v ?? 0));
    r.setAttribute("width", w);
    r.setAttribute("x", (v ?? 0) >= 0 ? 180 : 180 - w);
  };
  set("left", left);
  set("right", right);
}

const L1_SCALE = 1.2, L3_SCALE = 0.8;
// The gain meter spans 0 .. 1.25 x the calibrated upper bound, so real gains (~0.5) sit mid-way.
const l2Scale = () => (state.thresholds && state.thresholds.gain_range ? 1.25 * state.thresholds.gain_range[1] : 1.5);

function resetLayers() {
  layerCard("L1", { status: "idle", value: "—", sub: "Face and neck coherence appear here.", meter: 0 });
  layerCard("L2", { status: "idle", value: "—", sub: "Light should fade with distance: the wall behind barely reacts.", meter: 0 });
  layerCard("L3", { status: "idle", value: "—", sub: "A flat, re-rendered face shows no opposite lean.", meter: 0, cheeks: { left: 0, right: 0 } });
}

function liveLayers(m) {
  if (!m) return;
  const th = state.thresholds || {};
  const ratio = m.neck_coherence ? m.face_coherence / m.neck_coherence : null;
  layerCard("L1", {
    status: "live", value: fmt(ratio, 2),
    sub: `face ${fmt(m.face_coherence, 2)} · neck ${fmt(m.neck_coherence, 2)} · noise floor ${fmt(m.noise_floor, 2)}`,
    meter: (ratio ?? 0) / L1_SCALE, mark: th.face_to_neck_coherence_min / L1_SCALE,
  });
  layerCard("L2", {
    status: "live", value: fmt(m.differential_gain, 2),
    sub: "How strongly the face reacts compared with the neck.",
    meter: (m.differential_gain ?? 0) / l2Scale(), mark: th.gain_range ? th.gain_range[0] / l2Scale() : null,
  });
  if (m.cheek_contrast !== null && m.cheek_contrast !== undefined) {
    layerCard("L3", {
      status: "live", value: signed(m.cheek_contrast, 2),
      sub: "Live estimate over the last few seconds.",
      meter: m.cheek_contrast / L3_SCALE, mark: th.cheek_contrast_min != null ? th.cheek_contrast_min / L3_SCALE : null,
    });
  }
}

function resultLayers(result) {
  const checks = Object.fromEntries((result.verdict.checks || []).map((c) => [c.name, c]));
  const f = result.features || {};
  const m = f.measurement || {};
  const th = result.thresholds || state.thresholds || {};
  const notReached = `Not reached: ${result.verdict.reason}`;
  const st = (c) => (!c ? "skip" : c.passed === true ? "pass" : c.passed === false ? "fail" : "idle");

  const c1 = checks.L1_face_to_neck_coherence;
  layerCard("L1", {
    status: st(c1), value: c1 ? fmt(c1.value, 2) : "—",
    sub: c1 ? `face ${fmt(m.face?.coherence, 2)} · neck ${fmt(m.neck?.coherence, 2)} · needs ${c1.threshold}` : notReached,
    meter: c1 ? c1.value / L1_SCALE : 0, mark: th.face_to_neck_coherence_min ? th.face_to_neck_coherence_min / L1_SCALE : null,
  });

  const g = checks.L2_differential_gain, bg = checks.L2_background_falloff;
  const l2 = !g ? "skip" : (g.passed === false || (bg && bg.passed === false)) ? "fail" : "pass";
  layerCard("L2", {
    status: l2, value: g ? fmt(g.value, 2) : "—",
    sub: g ? `gain needs ${g.threshold} · background reacts ${bg && bg.value !== null ? fmt(bg.value, 2) : "–"} of the neck (≤ ${th.background_gain_ratio_max ?? "–"})` : notReached,
    meter: g ? g.value / l2Scale() : 0, mark: th.gain_range ? th.gain_range[0] / l2Scale() : null,
  });

  const c3 = checks.L3_cheek_contrast;
  const lat = f.lateral || {};
  const se = f.cheek_contrast_se;
  let sub3 = notReached;
  if (c3) {
    const need = c3.threshold.startsWith("needs") ? c3.threshold : `needs ${c3.threshold}`;
    sub3 = `${c3.value !== null && se != null ? `± ${fmt(se, 2)} · ` : ""}${need}`;
  }
  layerCard("L3", {
    status: c3 ? (c3.passed === null ? "idle" : st(c3)) : "skip",
    value: c3 && c3.value !== null ? signed(c3.value, 2) : "—",
    sub: sub3,
    meter: c3 && c3.value !== null ? c3.value / L3_SCALE : 0,
    mark: th.cheek_contrast_min != null ? th.cheek_contrast_min / L3_SCALE : null,
    cheeks: { left: lat["face.cheek_l"] ?? 0, right: lat["face.cheek_r"] ?? 0 },
  });
}

// ---------------------------------------------------------------- safeguards
const GATES = [
  { title: "Good data", desc: "camera fast enough, long enough, face visible", names: ["capture_fps", "duration_s", "max_gap_s", "face_fraction"] },
  { title: "Head & distance", desc: "facing the screen, as close as in calibration", names: ["head_yaw", "face_size_px"] },
  { title: "Light reaches them", desc: "the neck follows today's pattern", names: ["neck_coherence"] },
  { title: "Not a replay", desc: "not following an old pattern", names: ["face_neck_coherence"], reject: true },
  { title: "No lamp on the face", desc: "face/neck brightness as in calibration", names: ["face_neck_lightness"] },
];

function renderGates(checks) {
  const by = Object.fromEntries((checks || []).map((c) => [c.name, c]));
  $("#gates").innerHTML = GATES.map((g) => {
    const present = g.names.map((n) => by[n]).filter(Boolean);
    const failed = present.find((c) => c.passed === false);
    let cls = "", note = g.desc;
    if (!checks) note = g.desc;
    else if (failed) { cls = `fail${g.reject ? " reject" : ""}`; note = `${failed.name} = ${fmt(failed.value)} (needs ${failed.threshold})`; }
    else if (present.length) { cls = "pass"; }
    else note = `${g.desc} · not checked`;
    return `<div class="gate ${cls}"><span class="dot"></span><div><b>${esc(g.title)}</b><span>${esc(note)}</span></div></div>`;
  }).join("");
}

function renderChecks(checks) {
  $("#checks tbody").innerHTML = (checks || []).map((c) => {
    const cls = c.passed === true ? "pass" : c.passed === false ? "fail" : "na";
    const mark = c.passed === true ? "pass" : c.passed === false ? "FAIL" : "–";
    return `<tr><td>${esc(c.name)}</td><td class="num">${c.value === null ? "–" : fmt(c.value)}</td><td>${esc(c.threshold)}</td><td class="${cls}">${mark}</td></tr>`;
  }).join("");
}

// ---------------------------------------------------------------- live polling
async function poll() {
  clearTimeout(state.timer);
  if (!state.sessionId) return;
  let snap;
  try {
    snap = await api(`/api/sessions/${state.sessionId}/live?since=${state.next}`);
  } catch (e) {
    setHero("abstain", "Error", e.message);
    return;
  }
  state.next = snap.next;
  state.samples.push(...snap.samples);
  const keepFrom = state.samples.length - 30 * (WINDOW_S + 5);
  if (keepFrom > 0) state.samples.splice(0, keepFrom);

  const total = WARMUP_S + snap.duration_s;
  $("#progress-bar").style.width = `${100 * clamp01((snap.elapsed_s + WARMUP_S) / total)}%`;
  drawTraces();
  showTiles(snap);

  if (snap.result) {
    showResult(snap.result);
    $("#start-btn").disabled = false;
    $("#stop-btn").disabled = true;
    loadSessions();
    return;
  }
  const phase = snap.state === "analysing" ? "analysis" : snap.elapsed_s < 0 ? "warmup" : "probe";
  setStep(phase);
  setHero("running", phase === "warmup" ? "Warming up" : phase === "probe" ? "Measuring" : "Analysing", PHASE_TEXT[phase]);
  liveLayers(snap.metrics);
  state.timer = setTimeout(poll, 300);
}

function showTiles(snap) {
  const m = snap.metrics || {};
  const tiles = [
    ["camera", `${fmt(snap.capture_fps, 1)} fps`],
    ["frames dropped", String(snap.dropped ?? "–")],
    ["light delay", m.lag_s === undefined ? "–" : `${Math.round(m.lag_s * 1000)} ms`],
    ["noise floor", fmt(m.noise_floor, 3)],
    ["face found", m.face_fraction === undefined ? "–" : `${Math.round(m.face_fraction * 100)}%`],
    ["probe", snap.layout === "split" ? "two-sided" : "single"],
  ];
  $("#tiles").innerHTML = tiles.map(([k, v]) => `<div class="tile"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("");
}

// Each trace is drawn as a deviation from its own recent mean, scaled to its own range: what
// matters is whether face and neck move with the light, not their absolute colours.
function drawTraces() {
  const canvas = $("#traces");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = cssVar("--line");
  ctx.lineWidth = 1;
  for (const y of [h / 3, (2 * h) / 3]) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  const rows = state.samples;
  if (!rows.length) {
    ctx.fillStyle = cssVar("--faint");
    ctx.font = "13px Segoe UI, system-ui, sans-serif";
    ctx.fillText("The light pattern and the face's response will scroll here during a session.", 14, h / 2);
    return;
  }
  const tEnd = rows[rows.length - 1].t, tStart = tEnd - WINDOW_S;
  const series = [
    ["--probe", (r) => (r.probe ? r.probe[0] : null), 1 / 6],
    ["--face", (r) => (r.face ? r.face[0] : null), 3 / 6],
    ["--neck", (r) => (r.neck ? r.neck[0] : null), 5 / 6],
  ];
  for (const [colour, get, centre] of series) {
    const pts = rows.filter((r) => r.t >= tStart).map((r) => [r.t, get(r)]).filter(([, v]) => v !== null);
    if (pts.length < 2) continue;
    const vals = pts.map(([, v]) => v);
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const span = Math.max(1e-6, ...vals.map((v) => Math.abs(v - mean)));
    ctx.strokeStyle = cssVar(colour);
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    pts.forEach(([t, v], i) => {
      const x = ((t - tStart) / WINDOW_S) * w;
      const y = centre * h - ((v - mean) / span) * (h * 0.14);
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
  }
}

// ---------------------------------------------------------------- results
const WORDS = { verify: "VERIFIED", reject: "REJECTED", abstain: "NO VERDICT" };
const LEAD = {
  verify: "A real person is physically present: ",
  reject: "Not a physically present face: ",
  abstain: "Not enough evidence to decide, so LUMEN refuses to guess: ",
};

function showResult(result) {
  const v = result.verdict;
  const badges = [];
  if (v.provisional) badges.push(["prov", "PROVISIONAL THRESHOLDS"]);
  if (result.capture && result.capture.simulated) badges.push(["sim", "SIMULATED"]);
  setStep("verdict");
  setHero(v.decision, WORDS[v.decision] || v.decision.toUpperCase(), LEAD[v.decision] + v.reason, badges);
  $("#progress-bar").style.width = "100%";
  resultLayers(result);
  renderGates(v.checks);
  renderChecks(v.checks);
  const actions = $("#record-actions");
  actions.innerHTML = "";
  if (result.record_path) {
    const id = encodeURIComponent(result.session_id);
    actions.innerHTML =
      `<a class="btn ghost small" href="/api/sessions/${id}/record">⤓ Download signed record</a>` +
      `<button class="btn ghost small" type="button" id="verify-this">✓ Check this record</button>`;
    $("#verify-this").addEventListener("click", async () => {
      const rec = await fetch(`/api/sessions/${id}/record`).then((r) => r.text());
      showVerify(await api("/api/records/verify", { method: "POST", body: rec }));
      $("#verify-out").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }
}

function resetResult() {
  setStep("warmup");
  setHero("running", "Starting", "Opening the camera and the probe window…");
  resetLayers();
  renderGates(null);
  renderChecks([]);
  $("#record-actions").innerHTML = "";
  $("#progress-bar").style.width = "0";
}

// ---------------------------------------------------------------- history + calibration
function when(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function loadSessions() {
  const rows = await api("/api/sessions").catch(() => []);
  $("#sessions tbody").innerHTML = rows.map((r) => {
    const verdict = r.decision ? `<span class="chip ${esc(r.decision)}">${esc(r.decision.toUpperCase())}</span>`
                               : `<span class="chip running">${esc(r.state || "running")}</span>`;
    const sim = r.simulated ? '<span class="chip sim">SIM</span>' : "";
    const name = r.label ? `<b>${esc(r.label)}</b> <span class="hint mono">${esc(r.session_id.slice(-4))}</span>` : `<span class="mono">${esc(r.session_id)}</span>`;
    return `<tr title="${esc(r.reason || "")}"><td><input type="checkbox" value="${esc(r.session_id)}" ${r.decision ? "" : "disabled"}></td>` +
      `<td>${esc(when(r.started_at))}</td><td>${name}</td><td>${esc((r.source || "").replace(/ \(.*\)$/, ""))}${sim}</td>` +
      `<td>${verdict}</td><td class="num">${fmt(r.face_coherence, 2)}</td><td class="num">${fmt(r.neck_coherence, 2)}</td>` +
      `<td class="num">${signed(r.cheek_contrast, 2)}</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="hint">No sessions yet.</td></tr>`;
  loadThresholds();
}

$("#refresh-btn").addEventListener("click", loadSessions);

$("#calibrate-btn").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#sessions input:checked")].map((el) => el.value);
  if (ids.length < 3) {
    alert("Tick at least three genuine sessions recorded in this room and light.");
    return;
  }
  try {
    await api("/api/calibrate", { method: "POST", body: JSON.stringify({ session_ids: ids }) });
    await loadSessions();
  } catch (e) {
    alert(`Calibration failed: ${e.message}`);
  }
});

$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Discard the calibration and go back to provisional thresholds?")) return;
  await api("/api/thresholds", { method: "DELETE" });
  loadSessions();
});

// ---------------------------------------------------------------- verify a record
function showVerify(out) {
  $("#verify-out").innerHTML = out.ok
    ? '<span class="ok">✓ Genuine record.</span> Signed by this console, unmodified, and the light it logs matches its one-time code.'
    : `<span class="bad">✕ Record does not check out.</span> ${esc((out.problems || []).join("; "))}`;
}

async function verifyFile(file) {
  if (!file) return;
  try {
    showVerify(await api("/api/records/verify", { method: "POST", body: await file.text() }));
  } catch (e) {
    $("#verify-out").innerHTML = `<span class="bad">Could not check it:</span> ${esc(e.message)}`;
  }
}

const drop = $("#drop");
$("#verify-file").addEventListener("change", (e) => verifyFile(e.target.files[0]));
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  verifyFile(e.dataTransfer.files[0]);
});

// ---------------------------------------------------------------- start
resetLayers();
renderGates(null);
showTiles({ capture_fps: null, dropped: "–", layout: "split", metrics: {} });
loadHealth();
loadSessions();
drawTraces();
window.addEventListener("resize", drawTraces);
// ?session=<id> follows a session started elsewhere (e.g. from another tab) until its verdict.
const followId = new URLSearchParams(location.search).get("session");
if (followId) {
  Object.assign(state, { sessionId: followId, next: 0, samples: [] });
  poll();
}
