/* LUMEN verification page: draw the probe, report when each frame was drawn, upload the camera.
 * All measuring happens on the server; see src/lumen/web.py. Times are performance.now() seconds,
 * one clock for both draw times and camera capture times. */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const MAX_IN_FLIGHT = 24;
  const JPEG_QUALITY = 0.92;

  let stream = null, preflightOk = false;

  async function openCamera(deviceId) {
    if (stream) stream.getTracks().forEach(t => t.stop());
    const video = { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 } };
    if (deviceId) video.deviceId = { exact: deviceId };
    stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    $("preview").srcObject = stream;
    await $("preview").play();
    return stream;
  }

  async function listCameras(selected) {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput");
    $("cam").innerHTML = "";
    devices.forEach((d, i) => {
      const o = document.createElement("option");
      o.value = d.deviceId; o.textContent = d.label || `Camera ${i + 1}`;
      if (d.deviceId === selected) o.selected = true;
      $("cam").appendChild(o);
    });
  }

  // Pre-flight: the camera delivers fresh frames fast enough (a hidden or frozen source fails).
  function preflight() {
    const v = $("preview");
    const status = $("preflight");
    status.className = "status"; status.textContent = "Checking the camera…";
    preflightOk = false; updateStart();
    if (!("requestVideoFrameCallback" in HTMLVideoElement.prototype)) {
      status.className = "status bad"; status.textContent = "Please use Chrome or Edge (this browser cannot time camera frames).";
      return;
    }
    const canvas = document.createElement("canvas"); canvas.width = 32; canvas.height = 24;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    let n = 0, changed = 0, prev = null; const t0 = performance.now();
    function tick() {
      ctx.drawImage(v, 0, 0, 32, 24);
      const px = ctx.getImageData(0, 0, 32, 24).data;
      if (prev) { let d = 0; for (let i = 0; i < px.length; i += 4) d += Math.abs(px[i] - prev[i]); if (d > 0) changed++; }
      prev = px.slice(); n++;
      if (performance.now() - t0 < 2500) { v.requestVideoFrameCallback(tick); return; }
      const fps = n / 2.5, live = changed / Math.max(1, n - 1);
      if (fps < 16) { status.className = "status bad"; status.textContent = `Camera too slow (${fps.toFixed(0)} fps, needs 16): add light or pick another camera.`; }
      else if (live < 0.3) { status.className = "status bad"; status.textContent = "The camera picture looks frozen: check the camera source."; }
      else { status.className = "status ok"; status.textContent = `Camera OK: ${fps.toFixed(0)} fps, ${v.videoWidth}x${v.videoHeight}.`; preflightOk = true; }
      updateStart();
    }
    v.requestVideoFrameCallback(tick);
  }

  function updateStart() { $("start").disabled = !(preflightOk && $("ready").checked); }

  async function run() {
    const camLabel = $("cam").selectedOptions[0] ? $("cam").selectedOptions[0].textContent : "?";
    const res = await fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify({ label: "web" }) });
    if (!res.ok) { alert(`Could not start: ${(await res.json()).detail || res.status}`); return; }
    const probe = await res.json();

    $("intro").hidden = true; $("probe").hidden = false;
    try { await $("probe").requestFullscreen(); } catch (e) { /* still runs, just not full screen */ }

    const t0 = performance.now() / 1000 + probe.warmup_s;
    const tEnd = t0 + probe.duration_s;
    const drawn = [];
    let stop = false, aborted = false, inFlight = 0, dropped = 0, sent = 0;
    const onKey = e => { if (e.key === "Escape") { stop = true; aborted = true; } };
    window.addEventListener("keydown", onKey);

    // camera upload: every new frame, stamped with its capture time
    const v = $("preview");
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    function onFrame(now, meta) {
      if (stop) return;
      const t = (meta.captureTime !== undefined ? meta.captureTime : (meta.expectedDisplayTime || now)) / 1000;
      if (inFlight >= MAX_IN_FLIGHT) { dropped++; v.requestVideoFrameCallback(onFrame); return; }
      canvas.width = v.videoWidth; canvas.height = v.videoHeight;
      ctx.drawImage(v, 0, 0);
      inFlight++;
      canvas.toBlob(blob => {
        fetch(`/api/sessions/${probe.session_id}/frame`, { method: "POST", body: blob,
          headers: { "X-Capture-T": String(t), "Content-Type": "image/jpeg" } })
          .then(() => { sent++; }).catch(() => { dropped++; }).finally(() => { inFlight--; });
      }, "image/jpeg", JPEG_QUALITY);
      v.requestVideoFrameCallback(onFrame);
    }
    v.requestVideoFrameCallback(onFrame);

    // probe drawing: colour for the exact moment of each animation frame
    const left = $("left"), right = $("right"), hud = $("hud");
    await new Promise(resolve => {
      function draw(ts) {
        const now = ts / 1000, pt = now - t0;
        const css = LumenProbe.cssAt(probe, pt);
        left.style.background = css[0];
        right.style.background = css[css.length - 1];
        if (pt >= 0 && pt <= probe.duration_s) drawn.push(now);
        hud.textContent = pt < 0 ? `starting in ${Math.ceil(-pt)} s, keep still` : `${Math.ceil(probe.duration_s - pt)} s`;
        if (stop || now > tEnd) { resolve(); return; }
        requestAnimationFrame(draw);
      }
      requestAnimationFrame(draw);
    });
    stop = true;
    window.removeEventListener("keydown", onKey);
    hud.textContent = "measuring…";
    const waitUntil = performance.now() + 15000;
    while (inFlight > 0 && performance.now() < waitUntil) await new Promise(r => setTimeout(r, 100));
    if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});

    const fin = await fetch(`/api/sessions/${probe.session_id}/finish`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ t0, drawn_t: drawn, aborted,
        client: { camera: camLabel, dropped, sent, user_agent: navigator.userAgent } }) });
    showResult(fin.ok ? await fin.json() : { verdict: "error", reason: `server error ${fin.status}` });
  }

  function fmt(v) { return typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(3)) : (v === null ? "–" : String(v)); }

  function showResult(r) {
    $("probe").hidden = true; $("result").hidden = false;
    $("verdict").textContent = (r.verdict || "error").toUpperCase() + (r.provisional ? " (provisional)" : "");
    $("verdict").className = r.verdict;
    $("reason").textContent = r.reason || "";
    $("checks").innerHTML = (r.checks || []).map(c =>
      `<tr><td>${c.name}</td><td>${fmt(c.value)}</td><td class="${c.passed === true ? "pass" : c.passed === false ? "fail" : "na"}">` +
      `${c.passed === true ? "pass" : c.passed === false ? "FAIL" : "n/a"}</td><td class="small">${c.threshold}</td></tr>`).join("");
    $("deep").textContent = r.deep_check ? `Deep check (3D relief + lean): ${r.deep_check}` : "";
    $("meta").textContent = r.session_id ? `Session ${r.session_id}: ${r.frames} frames at ${fmt(r.capture_fps)} fps, face found in ${Math.round(100 * (r.face_fraction || 0))}%.` : "";
    if (r.record) { $("download").href = r.record; $("download").hidden = false; }
  }

  $("ready").addEventListener("change", updateStart);
  $("cam").addEventListener("change", async () => { await openCamera($("cam").value); preflight(); });
  $("start").addEventListener("click", () => { $("start").disabled = true; run().catch(e => showResult({ verdict: "error", reason: String(e) })); });
  $("again").addEventListener("click", () => { $("result").hidden = true; $("intro").hidden = false; $("ready").checked = false; preflight(); });

  (async () => {
    try {
      const s = await openCamera();
      await listCameras(s.getVideoTracks()[0].getSettings().deviceId);
      preflight();
    } catch (e) {
      $("preflight").className = "status bad";
      $("preflight").textContent = window.isSecureContext ? `No camera: ${e.message}` :
        "The camera needs a secure page: open the https:// address (or localhost).";
    }
  })();
})();
