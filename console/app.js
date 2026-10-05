// LUMEN operator console. Plain JS, no dependencies, so it runs with the network unplugged.
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { sessionId: null, next: 0, samples: [], timer: null };
const WINDOW_S = 10; // seconds of trace on screen

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

const fmt = (v, d = 3) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// --- health and thresholds -----------------------------------------------------------------

async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("#health").innerHTML = `v${esc(h.version)} · key ${esc(h.public_key.slice(0, 12))}… · ` +
      (h.thresholds_provisional ? '<span class="badge prov">provisional thresholds</span>' : "calibrated thresholds");
  } catch (e) {
    $("#health").textContent = `service unreachable: ${e.message}`;
  }
}

async function loadThresholds() {
  const th = await api("/api/thresholds");
  const l3 = th.cheek_contrast_min === null ? "L3 not calibrated" : `L3 cheek contrast ≥ ${th.cheek_contrast_min}`;
  $("#thresholds").textContent = `${th.source}. ${l3}.`;
}

// --- start / stop --------------------------------------------------------------------------

$("#start-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const body = {
    source: f.get("source"),
    layout: f.get("layout"),
    duration_s: Number(f.get("duration_s")),
    amplitude: Number(f.get("amplitude")),
    label: f.get("label") || "",
    fullscreen: f.get("fullscreen") === "on",
    save_video: f.get("save_video") === "on",
  };
  try {
    const { session_id } = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    Object.assign(state, { sessionId: session_id, next: 0, samples: [] });
    $("#start-btn").disabled = true;
    $("#stop-btn").disabled = false;
    showVerdict(null);
    poll();
  } catch (e) {
    alert(`Could not start: ${e.message}`);
  }
});

$("#stop-btn").addEventListener("click", async () => {
  if (state.sessionId) await api(`/api/sessions/${state.sessionId}/stop`, { method: "POST" }).catch(() => {});
});

// --- live polling --------------------------------------------------------------------------

async function poll() {
  clearTimeout(state.timer);
  if (!state.sessionId) return;
  let snap;
  try {
    snap = await api(`/api/sessions/${state.sessionId}/live?since=${state.next}`);
  } catch (e) {
    $("#live-state").textContent = `error: ${e.message}`;
    return;
  }
  state.next = snap.next;
  state.samples.push(...snap.samples);
  const keepFrom = state.samples.length - 30 * (WINDOW_S + 5);
  if (keepFrom > 0) state.samples.splice(0, keepFrom);

  $("#live-state").textContent = snap.state;
  $("#progress-bar").style.width = `${Math.max(0, Math.min(100, (100 * snap.elapsed_s) / snap.duration_s))}%`;
  drawTraces();
  showTiles(snap);

  if (snap.result) {
    showVerdict(snap.result);
    $("#start-btn").disabled = false;
    $("#stop-btn").disabled = true;
    loadSessions();
    return;
  }
  state.timer = setTimeout(poll, 250);
}

function showTiles(snap) {
  const m = snap.metrics || {};
  const tiles = [
    ["face coherence", fmt(m.face_coherence)],
    ["neck coherence", fmt(m.neck_coherence)],
    ["noise floor", fmt(m.noise_floor)],
    ["face / neck gain", fmt(m.differential_gain, 2)],
    ["cheek contrast", fmt(m.cheek_contrast)],
    ["lag", m.lag_s === undefined ? "–" : `${Math.round(m.lag_s * 1000)} ms`],
    ["camera", `${fmt(snap.capture_fps, 1)} fps`],
    ["dropped", String(snap.dropped)],
  ];
  $("#tiles").innerHTML = tiles.map(([k, v]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
}

// Each trace is shown as a deviation from its own recent mean, scaled to its own range: the
// point is to see whether face and neck move with the probe, not their absolute colours.
function drawTraces() {
  const canvas = $("#traces");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const rows = state.samples;
  if (!rows.length) return;
  const tEnd = rows[rows.length - 1].t, tStart = tEnd - WINDOW_S;
  const series = [
    ["--probe", (r) => (r.probe ? r.probe[0] : null), 0.17],
    ["--face", (r) => (r.face ? r.face[0] : null), 0.5],
    ["--neck", (r) => (r.neck ? r.neck[0] : null), 0.83],
  ];
  for (const [colour, get, centre] of series) {
    const pts = rows.filter((r) => r.t >= tStart).map((r) => [r.t, get(r)]).filter(([, v]) => v !== null);
    if (pts.length < 2) continue;
    const vals = pts.map(([, v]) => v);
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const span = Math.max(1e-6, ...vals.map((v) => Math.abs(v - mean)));
    ctx.strokeStyle = cssVar(colour);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    pts.forEach(([t, v], i) => {
      const x = ((t - tStart) / WINDOW_S) * w;
      const y = centre * h - ((v - mean) / span) * (h * 0.14);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  }
}

// --- verdict -------------------------------------------------------------------------------

function showVerdict(result) {
  const box = $("#verdict"), tbody = $("#checks tbody");
  tbody.innerHTML = "";
  $("#record-links").innerHTML = "";
  if (!result) {
    box.className = "verdict empty";
    box.textContent = "Running…";
    return;
  }
  const v = result.verdict;
  const simulated = result.capture && result.capture.simulated;
  box.className = `verdict ${v.decision}`;
  box.innerHTML = esc(v.decision.toUpperCase()) +
    (v.provisional ? '<span class="badge prov">provisional</span>' : "") +
    (simulated ? '<span class="badge sim">SIMULATED</span>' : "") +
    `<div class="reason">${esc(v.reason)}</div>`;
  for (const c of v.checks) {
    const cls = c.passed === true ? "pass" : c.passed === false ? "fail" : "na";
    const mark = c.passed === true ? "pass" : c.passed === false ? "FAIL" : "–";
    tbody.insertAdjacentHTML("beforeend",
      `<tr><td>${esc(c.name)}</td><td>${c.value === null ? "–" : fmt(c.value)}</td><td>${esc(c.threshold)}</td><td class="${cls}">${mark}</td></tr>`);
  }
  if (result.record_path) {
    $("#record-links").innerHTML = `<a href="/api/sessions/${encodeURIComponent(result.session_id)}/record">Download signed record</a>`;
  }
}

// --- sessions, calibration -----------------------------------------------------------------

async function loadSessions() {
  const rows = await api("/api/sessions").catch(() => []);
  $("#sessions tbody").innerHTML = rows.map((r) => {
    const d = r.decision ? `<span class="d-${esc(r.decision)}">${esc(r.decision)}</span>` : esc(r.state || "");
    const sim = r.simulated ? ' <span class="badge sim">SIM</span>' : "";
    return `<tr><td><input type="checkbox" value="${esc(r.session_id)}" ${r.decision ? "" : "disabled"}></td>` +
      `<td>${esc(r.session_id)}${sim}</td><td>${esc(r.source)}</td><td>${esc(r.layout || "")}</td><td>${d}</td>` +
      `<td>${fmt(r.face_coherence)}</td><td>${fmt(r.neck_coherence)}</td><td>${fmt(r.cheek_contrast)}</td></tr>`;
  }).join("");
  loadThresholds();
  loadHealth();
}

$("#refresh-btn").addEventListener("click", loadSessions);

$("#calibrate-btn").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#sessions input:checked")].map((el) => el.value);
  if (!ids.length) return alert("Select genuine sessions recorded in today's room and light.");
  try {
    await api("/api/calibrate", { method: "POST", body: JSON.stringify({ session_ids: ids }) });
    loadSessions();
  } catch (e) {
    alert(`Calibration failed: ${e.message}`);
  }
});

$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Discard calibrated thresholds and return to provisional defaults?")) return;
  await api("/api/thresholds", { method: "DELETE" });
  loadSessions();
});

$("#verify-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  try {
    const out = await api("/api/records/verify", { method: "POST", body: await file.text() });
    $("#verify-out").innerHTML = out.ok
      ? '<span class="d-verify">Valid</span>: signed by this console, and the emission matches its one-time code.'
      : `<span class="d-reject">Invalid</span>: ${esc(out.problems.join("; "))}`;
  } catch (e) {
    $("#verify-out").textContent = `Could not verify: ${e.message}`;
  }
});

loadSessions();
window.addEventListener("resize", drawTraces);
