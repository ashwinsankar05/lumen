/* LUMEN verification page: draw the probe, report when each frame was drawn, upload the camera.
 * All measuring happens on the server; see src/lumen/web.py. Times are performance.now() seconds,
 * one clock for both draw times and camera capture times. */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const MAX_IN_FLIGHT = 24;
  const JPEG_QUALITY = 0.92;

  let stream = null, preflightOk = false, speedTried = false, speedNote = "", exposureBright = null;
  const MIN_BRIGHT = 20;  // mean picture brightness (0-255) below which a face is not reliably found

  // Frame rate and brightness (mean of the small preview, 0-255) over `ms` milliseconds.
  function measureCamera(ms) {
    const v = $("preview");
    const canvas = document.createElement("canvas"); canvas.width = 32; canvas.height = 24;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    return new Promise(resolve => {
      let n = 0, sum = 0; const t0 = performance.now();
      function tick() {
        ctx.drawImage(v, 0, 0, 32, 24);
        const px = ctx.getImageData(0, 0, 32, 24).data;
        let s = 0; for (let i = 0; i < px.length; i += 4) s += (px[i] + px[i + 1] + px[i + 2]) / 3;
        sum += s / (px.length / 4); n++;
        if (performance.now() - t0 < ms) v.requestVideoFrameCallback(tick);
        else resolve({ fps: n / (ms / 1000), bright: sum / n });
      }
      v.requestVideoFrameCallback(tick);
    });
  }

  // A dim room makes auto exposure lengthen each frame (the dev laptop's webcam drops to ~10 fps,
  // below the 16 the 8 Hz probe needs). Exposure units differ between cameras and drivers, so
  // rather than guess a value, try exposures from long to short and keep the longest (brightest)
  // that still gives >= 20 fps. Returns a note for the status line.
  const FAST_ENOUGH = 20;
  async function tuneExposure(status) {
    const track = stream && stream.getVideoTracks()[0];
    const caps = track && track.getCapabilities ? track.getCapabilities() : {};
    if (!caps.exposureMode || !caps.exposureMode.includes("manual") || !caps.exposureTime) {
      return "this camera does not let the browser set its exposure";
    }
    const r = caps.exposureTime, lo = Math.max(r.min, 1e-3), hi = r.max;
    // Cameras take a while to apply a new exposure: settle, then measure. Every setting is
    // measured (no early stop), so one misleading reading cannot pick a dark setting.
    const apply = async setting => {
      const c = setting === "auto" ? { exposureMode: "continuous" }
                                   : { exposureMode: "manual", exposureTime: setting };
      await track.applyConstraints({ advanced: [c] });
      await new Promise(res => setTimeout(res, 900));
      return measureCamera(1200);
    };
    try {
      const steps = 7, tried = [];
      for (let i = 0; i < steps; i++) {                   // log-spaced, long to short
        const x = hi * Math.pow(lo / hi, i / (steps - 1));
        status.textContent = `Tuning the camera (${i + 1}/${steps + 2})…`;
        tried.push({ setting: x, ...(await apply(x)) });
      }
      // automatic exposure resumed from a short setting: on the dev laptop's webcam this kept a
      // fast frame rate natively (capture.py), so it is a candidate too
      status.textContent = `Tuning the camera (${steps + 1}/${steps + 2})…`;
      await apply(lo);
      tried.push({ setting: "auto", ...(await apply("auto")) });
      console.table(tried);
      const fast = tried.filter(m => m.fps >= FAST_ENOUGH);
      const best = fast.length ? fast.reduce((a, b) => (b.bright > a.bright ? b : a))
                               : tried.reduce((a, b) => (b.fps > a.fps ? b : a));
      status.textContent = `Tuning the camera (${steps + 2}/${steps + 2})…`;
      const m = await apply(best.setting);               // re-apply and confirm
      exposureBright = m.bright;
      const which = best.setting === "auto" ? "automatic exposure" : "fixed exposure";
      return `${which}: ${m.fps.toFixed(0)} fps, brightness ${m.bright.toFixed(0)}/255`;
    } catch (e) {
      return `could not set the exposure (${e.name})`;
    }
  }

  async function openCamera(deviceId) {
    if (stream) stream.getTracks().forEach(t => t.stop());
    const video = { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 } };
    if (deviceId) video.deviceId = { exact: deviceId };
    stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    // start from automatic exposure: a manual value set by an earlier visit can stick in the driver
    const track = stream.getVideoTracks()[0];
    const caps = track.getCapabilities ? track.getCapabilities() : {};
    if (caps.exposureMode && caps.exposureMode.includes("continuous")) {
      await track.applyConstraints({ advanced: [{ exposureMode: "continuous" }] }).catch(() => {});
    }
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
      if (fps < 16 && !speedTried) {
        speedTried = true;
        status.textContent = `Camera at ${fps.toFixed(0)} fps; tuning the exposure…`;
        tuneExposure(status).then(note => { speedNote = note; setTimeout(preflight, 500); });
        return;
      }
      if (fps < 16) { status.className = "status bad"; status.textContent = `Camera too slow (${fps.toFixed(0)} fps, needs 16; ${speedNote}): add light on your face or pick another camera.`; }
      else if (exposureBright !== null && exposureBright < MIN_BRIGHT) { status.className = "status bad"; status.textContent = `Too dark to see a face (${speedNote}): add light on your face from the front, then reload the page.`; }
      else if (live < 0.3) {
        status.className = "status bad";
        const cam = $("cam").selectedOptions[0] ? $("cam").selectedOptions[0].textContent : "";
        status.textContent = /virtual|obs/i.test(cam)
          ? "The virtual camera shows a still or black picture: check that the program feeding it shows a moving " +
            "picture (e.g. FaceFusion lost the webcam to another tab: restart its webcam), that OBS shows it, and " +
            "that 'Start Virtual Camera' is on; then reload this page."
          : "The camera picture looks frozen: check the camera source (another tab or app may be using it).";
      }
      else { status.className = "status ok"; status.textContent = `Camera OK: ${fps.toFixed(0)} fps, ${v.videoWidth}x${v.videoHeight}${speedTried ? ` (${speedNote})` : ""}.`; preflightOk = true; }
      updateStart();
    }
    v.requestVideoFrameCallback(tick);
  }

  // Neck check: once a second, the server finds the face and neck regions on a camera frame and says
  // whether the neck region is on skin; the outlines are drawn over the preview (green face, red neck).
  // One frame decides nothing: the level comes from the median of the last NECK_WINDOW checks, and a
  // missing face only counts after NECK_MISSES checks in a row.
  const NECK_WINDOW = 5, NECK_MISSES = 3;
  let neckOk = false, neckTimer = null, neckRatios = [], neckMisses = 0, neckLevel = "bad";
  function resetNeck() { neckRatios = []; neckMisses = 0; neckOk = false; neckLevel = "bad"; }
  function median(xs) { const s = [...xs].sort((a, b) => a - b); return s[Math.floor(s.length / 2)]; }
  function neckLevelFor(ratio, limit, skin) { return ratio <= limit ? "ok" : ratio <= skin ? "warn" : "bad"; }
  function neckText(level, ratio, limit) {
    if (level === "ok") return `Neck OK (face/neck brightness ${ratio.toFixed(2)}, limit ${limit.toFixed(2)}).`;
    if (level === "warn") return `Neck found, but the face is a little brighter than the neck (${ratio.toFixed(2)}, ` +
      `limit ${limit.toFixed(2)}): face the screen evenly and avoid light from above. You can start; the session may abstain.`;
    return `The neck box is on clothing or in shadow (face/neck brightness ${ratio.toFixed(2)}): sit a little ` +
      "further back, chin level, so bare neck shows below the chin, or lower the collar.";
  }
  const LEVEL_COLOUR = { ok: "#6cc4a1", warn: "#e2b96d", bad: "#e26d6d" };
  function drawOutlines(r) {
    const v = $("preview"), cv = $("overlay");
    cv.width = cv.clientWidth; cv.height = cv.clientHeight;
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (!r || !r.outlines || !v.videoWidth) return;
    const s = Math.min(cv.width / v.videoWidth, cv.height / v.videoHeight);
    const ox = (cv.width - v.videoWidth * s) / 2, oy = (cv.height - v.videoHeight * s) / 2;
    for (const [name, pts] of Object.entries(r.outlines)) {
      ctx.strokeStyle = name === "neck" ? LEVEL_COLOUR[neckLevel] : "#7fd37f";
      ctx.lineWidth = 3; ctx.beginPath();
      pts.forEach(([x, y], i) => { const px = ox + x * v.videoWidth * s, py = oy + y * v.videoHeight * s;
        if (i) ctx.lineTo(px, py); else ctx.moveTo(px, py); });
      ctx.closePath(); ctx.stroke();
    }
  }
  async function neckCheck() {
    const v = $("preview"), status = $("neckcheck");
    if ($("intro").hidden || !v.videoWidth) return;
    const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    const blob = await new Promise(res => c.toBlob(res, "image/jpeg", 0.85));
    try {
      const cam = $("cam").selectedOptions[0] ? $("cam").selectedOptions[0].textContent : "";
      const r = await (await fetch("/api/check-frame", { method: "POST", body: blob,
                                                         headers: { "X-Camera": encodeURIComponent(cam) } })).json();
      if (!r.face || typeof r.ratio !== "number") {
        neckMisses++;
        if (neckMisses >= NECK_MISSES) {
          resetNeck(); neckMisses = NECK_MISSES;
          status.className = "status bad"; status.textContent = r.hint || "No face found: face the camera, in good light.";
          drawOutlines(null);
        }
      } else {
        neckMisses = 0;
        neckRatios.push(r.ratio);
        if (neckRatios.length > NECK_WINDOW) neckRatios.shift();
        const m = median(neckRatios);
        neckLevel = neckLevelFor(m, r.limit, r.skin_limit);
        neckOk = neckLevel !== "bad";
        const settling = neckRatios.length < 3 ? " (settling…)" : "";
        status.className = "status " + neckLevel;
        status.textContent = neckText(neckLevel, m, r.limit) + settling;
        drawOutlines(r);
      }
    } catch (e) {
      neckOk = false; status.className = "status bad"; status.textContent = "Neck check: cannot reach the server.";
    }
    updateStart();
  }
  function startNeckChecks() { if (!neckTimer) neckTimer = setInterval(neckCheck, 1000); neckCheck(); }
  function stopNeckChecks() { clearInterval(neckTimer); neckTimer = null; drawOutlines(null); }

  function updateStart() { $("start").disabled = !(preflightOk && neckOk && $("ready").checked); }

  async function run() {
    const camLabel = $("cam").selectedOptions[0] ? $("cam").selectedOptions[0].textContent : "?";
    stopNeckChecks();
    // full screen first: Chrome allows it only straight after the click, before any await
    const fullscreen = document.documentElement.requestFullscreen().catch(() => {});
    // ?label=genuine marks a genuine calibration session; ?label=swap-on etc. tags test sessions
    const tag = new URLSearchParams(location.search).get("label");
    const res = await fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify({ label: tag ? `web-${tag}` : "web", camera: camLabel }) });
    if (!res.ok) {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      alert(`Could not start: ${(await res.json()).detail || res.status}`); return;
    }
    const probe = await res.json();

    $("intro").hidden = true; $("probe").hidden = false;
    await fullscreen;  // if the browser refused, the probe still runs in the window

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
    $("deep").textContent = (r.deep_check ? `Deep check (3D relief + lean): ${r.deep_check}` : "") +
      (r.temporal ? `  |  Temporal check (EXPERIMENTAL, not part of the verdict): ${r.temporal}` : "");
    $("meta").textContent = r.session_id ? `Session ${r.session_id}: ${r.frames} frames at ${fmt(r.capture_fps)} fps, face found in ${Math.round(100 * (r.face_fraction || 0))}%.` : "";
    if (r.record) { $("download").href = r.record; $("download").hidden = false; }
  }

  $("ready").addEventListener("change", updateStart);
  $("cam").addEventListener("change", async () => { await openCamera($("cam").value); speedTried = false; exposureBright = null; resetNeck(); preflight(); });
  $("start").addEventListener("click", () => { $("start").disabled = true; run().catch(e => showResult({ verdict: "error", reason: String(e) })); });
  $("again").addEventListener("click", () => { $("result").hidden = true; $("intro").hidden = false; $("ready").checked = false; preflight(); startNeckChecks(); });

  (async () => {
    try {
      const s = await openCamera();
      await listCameras(s.getVideoTracks()[0].getSettings().deviceId);
      preflight();
      startNeckChecks();
    } catch (e) {
      $("preflight").className = "status bad";
      $("preflight").textContent = window.isSecureContext ? `No camera: ${e.message}` :
        "The camera needs a secure page: open the https:// address (or localhost).";
    }
  })();
})();
