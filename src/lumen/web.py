"""Verification link: the subject opens a web page; their browser shows the probe and streams the
camera to this server, which does all the measuring.

    python -m lumen.web                      https://<this laptop>:8443  (self-signed certificate)
    python -m lumen.web --http               http://localhost:8443       (this laptop only)

Who does what. The page only (1) draws the probe colours, (2) reports WHEN it drew each frame,
and (3) uploads camera frames with their capture times. The server picks the one-time code,
regenerates the colour of every reported draw time from it, finds face, neck and cheeks, runs
L1-L3, the abstain gates and the deep check, and signs the record. Nothing the page computes is
taken on trust: a modified page can only change pixels, so to pass it must produce video that
answers a code it saw seconds ago, the same bar as call mode.

Separate from the operator console (lumen.api, port 8000) and the demo; it reuses Session for
analysis, signing and saving, with frames from the browser instead of a local camera.
Thresholds are those of the web path (calibrate through it: PROJECT.md §4).
"""

from __future__ import annotations

import ipaddress
import queue
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from lumen.capture import clock
from lumen.decide import Thresholds, load_thresholds
from lumen.probe import EmissionLog
from lumen.regions import RegionExtractor
from lumen.session import Session, SessionConfig

ROOT = Path(__file__).resolve().parents[2]
PAGE_DIR = ROOT / "webverify"
MAX_ACTIVE = 4                 # sessions measuring at once
MAX_FRAME_BYTES = 2_000_000
MAX_SESSION_AGE_S = 600.0


class WebSession(Session):
    """A Session whose frames arrive from a browser, timestamped on the browser's clock."""

    def __init__(self, cfg: SessionConfig, *, thresholds: Thresholds | None = None,
                 extractor_factory=RegionExtractor):
        super().__init__(cfg, thresholds=thresholds, extractor_factory=extractor_factory)
        self.created = clock()
        self.extractor = extractor_factory()
        self._queue: queue.Queue = queue.Queue()
        self.received = 0
        self.undecodable = 0
        self.frame_size: tuple[int, int] | None = None
        self._video_t: list[float] = []
        self._writer = None
        self._worker = threading.Thread(target=self._work, name=f"lumen-web-{self.id}", daemon=True)
        self._worker.start()
        self.state = "running"
        self.calibration: Calibration | None = None
        self.extra_notes: list[str] = []

    def page_params(self) -> dict:
        """What the page needs to draw the probe: each sequence's frequencies, phases and scale."""
        p = self.probe
        return {
            "session_id": self.id, "layout": p.layout, "duration_s": p.duration_s,
            "warmup_s": self.cfg.warmup_s, "lightness": p.lightness, "amplitude": p.amplitude,
            "sequences": [{"freqs": s.freqs_hz.tolist(), "phase_a": s.phases[0].tolist(),
                           "phase_b": s.phases[1].tolist(), "scale_a": float(s.scale[0]),
                           "scale_b": float(s.scale[1])} for s in p.sequences],
        }

    def add_frame(self, t: float, jpeg: bytes) -> None:
        self.received += 1
        self._queue.put((float(t), jpeg))

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            t, jpeg = item
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                self.undecodable += 1
                continue
            self.frame_size = (img.shape[1], img.shape[0])
            started = clock()
            sample = self.extractor.process(img)
            pose = self.extractor.last_pose
            with self._lock:
                self._t.append(t)
                self._samples.append(sample)
                self._pose.append((pose.yaw, pose.roll_deg, pose.ipd_px) if sample and pose else None)
                self._proc_ms.append((clock() - started) * 1e3)
            if self.cfg.save_video:
                self._save(img, t)
        if self._writer is not None:
            self._writer.release()
            out = Path(self.cfg.out_dir) / self.id
            (out / "video_timestamps.csv").write_text("".join(f"{t!r}\n" for t in self._video_t))

    def _save(self, img, t) -> None:
        if self._writer is None:
            out = Path(self.cfg.out_dir) / self.id
            out.mkdir(parents=True, exist_ok=True)
            h, w = img.shape[:2]
            self._writer = cv2.VideoWriter(str(out / "video.mkv"), cv2.VideoWriter_fourcc(*"FFV1"),
                                           self.cfg.fs, (w, h))
        if (img.shape[1], img.shape[0]) == self.frame_size:
            self._writer.write(img)
            self._video_t.append(t)

    def finish(self, t0: float, drawn_t: list[float], client: dict | None = None, aborted: bool = False):
        """Close the session: the page's probe start (t0) and the times it drew probe frames."""
        client = client or {}
        self._queue.put(None)
        self._worker.join(timeout=120.0)
        # Uploads run in parallel, so frames can arrive out of order (measured: 5 and 19 of 2700
        # through OBS Virtual Camera): put them in capture-time order, dropping repeated times.
        with self._lock:
            order = sorted(range(len(self._t)), key=lambda i: self._t[i])
            keep = [i for n, i in enumerate(order) if n == 0 or self._t[i] > self._t[order[n - 1]]]
            reordered = len(keep) != len(self._t) or keep != list(range(len(self._t)))
            self._t = [self._t[i] for i in keep]
            self._samples = [self._samples[i] for i in keep]
            self._pose = [self._pose[i] for i in keep]
        self.log = EmissionLog(self.probe, t0=float(t0))
        last = -np.inf
        for t in sorted(float(x) for x in drawn_t):
            if t > last and 0.0 <= t - self.log.t0 <= self.probe.duration_s:
                self.log.emit(t)   # the server's own colour for that moment, from the code
                last = t
        with self._lock:
            ts = np.array(self._t)
        fps = float((len(ts) - 1) / (ts.max() - ts.min())) if len(ts) > 1 else 0.0
        notes = [f"browser camera: {client.get('camera', '?')}; {self.received} frames received, "
                 f"{self.undecodable} undecodable; page drew {len(self.log)} probe frames"]
        notes += self.extra_notes
        if reordered:
            notes.append("frames arrived out of order (parallel uploads) and were put in capture-time order")
        if client.get("dropped"):
            notes.append(f"page dropped {client['dropped']} frames before upload (network backlog)")
        w, h = self.frame_size or (0, 0)
        capture = {"source": f"browser:{client.get('camera', '?')}", "width": w, "height": h,
                   "fps_requested": 30.0, "fps_reported": round(fps, 2), "exposure_locked": False,
                   "white_balance_locked": None, "simulated": bool(client.get("simulated")),
                   "notes": ["browser camera: auto exposure/white balance not locked; the neck "
                             "reference cancels common-mode drift (PROJECT.md §8)"],
                   "user_agent": str(client.get("user_agent", ""))[:200]}
        outcome = "stopped" if aborted else "done"
        return self._finish(outcome, capture=capture, capture_error=None, notes=notes)


class Registry:
    """Live web sessions, by id, with an upper bound and an age limit."""

    def __init__(self):
        self._sessions: dict[str, WebSession] = {}
        self._lock = threading.Lock()

    def add(self, s: WebSession) -> None:
        with self._lock:
            now = clock()
            for sid in [k for k, v in self._sessions.items() if now - v.created > MAX_SESSION_AGE_S]:
                del self._sessions[sid]
            if len(self._sessions) >= MAX_ACTIVE:
                raise RuntimeError("too many sessions at once; try again in a minute")
            self._sessions[s.id] = s

    def get(self, sid: str) -> WebSession | None:
        with self._lock:
            return self._sessions.get(sid)

    def pop(self, sid: str) -> WebSession | None:
        with self._lock:
            return self._sessions.pop(sid, None)


NECK_LIGHTNESS_DEFAULT_MAX = 1.35  # before calibration; genuine neck-on-skin sessions measured 0.99-1.29


def check_neck(img, extractor, lock, th: Thresholds) -> dict:
    """Face and neck outlines (fractions of the frame) and whether the neck region sits on skin."""
    from lumen.regions import region_masks

    h, w = img.shape[:2]
    with lock:
        extractor._smoothed = None  # one still frame: no landmark smoothing across calls
        means = extractor.process(img)
        lm = extractor.last_landmarks()
    if not means or lm is None or "face" not in means or "neck" not in means:
        return {"face": False, "ok": False, "hint": "No face found: face the camera, in good light."}
    outlines = {}
    for name, m in region_masks(lm, img.shape).items():
        if name in ("face", "neck"):
            contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                c = max(contours, key=cv2.contourArea).reshape(-1, 2)
                outlines[name] = [[round(float(x) / w, 4), round(float(y) / h, 4)] for x, y in c]
    ratio = float(means["face"][0] / max(means["neck"][0], 1e-6))
    limit = th.face_neck_lightness_max or NECK_LIGHTNESS_DEFAULT_MAX
    # Two questions, two limits. Is the neck region on skin? A collar or shadow puts it far off
    # (measured 1.70-2.46 against genuine 0.84-1.47): skin_limit = limit + NECK_SKIN_MARGIN. Will the
    # lamp gate pass? That limit is tight on purpose (a measured lamp moved the ratio by only ~0.03).
    # The page smooths these over several checks; one frame decides nothing.
    skin_limit = limit + NECK_SKIN_MARGIN
    ok = ratio <= skin_limit
    lamp_ok = ratio <= limit
    return {"face": True, "ok": ok, "lamp_ok": lamp_ok, "ratio": round(ratio, 3), "limit": limit,
            "skin_limit": round(skin_limit, 4), "outlines": outlines, "hint": neck_hint(ratio, limit, skin_limit)}


NECK_SKIN_MARGIN = 0.35


def neck_hint(ratio: float, limit: float, skin_limit: float) -> str:
    if ratio <= limit:
        return f"Neck OK (face/neck brightness {ratio:.2f}, limit {limit:.2f})."
    if ratio <= skin_limit:
        return (f"Neck found, but the face is a little brighter than the neck ({ratio:.2f}, limit {limit:.2f}): "
                "face the screen evenly and avoid light from above. You can start; the session may abstain.")
    return (f"The neck box is on clothing or in shadow (face/neck brightness {ratio:.2f}): sit a little further "
            "back, chin level, so bare neck shows below the chin, or lower the collar.")


def _result_payload(r, deep_line: str | None) -> dict:
    v = r.verdict
    f = r.features
    return {
        "session_id": r.session_id,
        "verdict": v.decision, "reason": v.reason, "provisional": v.provisional,
        "checks": [{"name": c.name, "value": c.value, "threshold": c.threshold, "passed": c.passed}
                   for c in v.checks],
        "cheek_contrast": None if not f else f.cheek_contrast,
        "face_fraction": r.face_fraction, "capture_fps": r.capture.get("fps_reported"),
        "frames": r.frames_processed, "deep_check": deep_line, "notes": r.notes,
        "record": f"/api/sessions/{r.session_id}/record" if r.record_path else None,
    }


@dataclass(frozen=True)
class Calibration:
    """Which calibration judges a session: one per camera path, because calibration does not transfer
    between cameras (measured 2026-10-07: OBS window-capture frames show faces larger and re-timed,
    so webcam limits made OBS sessions abstain and OBS limits made webcam sessions abstain)."""
    name: str
    thresholds: str
    deep: str
    camera: str = ""            # matches when the camera label contains this (case-insensitive); "" = any
    remote: bool | None = None  # True: another laptop; False: this laptop; None: either


def load_calibrations(path: str | Path) -> list[Calibration]:
    import json

    return [Calibration(**c) for c in json.loads(Path(path).read_text())]


def pick_calibration(cals: list[Calibration], camera: str, remote: bool) -> Calibration:
    """The first rule that matches. A camera label is chosen by the subject's browser, so it can only
    select among genuine calibrations, all with L4; it cannot switch any check off."""
    for c in cals:
        if c.camera and c.camera.lower() not in (camera or "").lower():
            continue
        if c.remote is not None and c.remote != remote:
            continue
        return c
    return cals[-1]


def _is_remote(request) -> bool:
    """Another laptop? Behind a Cloudflare tunnel every request arrives from the local cloudflared
    process, so the visitor is recognised by the header Cloudflare adds."""
    if request.headers.get("CF-Connecting-IP"):
        return True
    host = request.client.host if request.client else ""
    return host not in ("127.0.0.1", "::1", "localhost", "testclient")


def create_app(*, out_dir: str = "data/web/sessions", thresholds_path: str = "data/thresholds-web.json",
               deep_thresholds_path: str = "data/web/deep_thresholds.json",
               key_path: str = "data/keys/lumen_ed25519.pem", duration_s: float = 90.0,
               amplitude: float = 34.0, save_video: bool = True, extractor_factory=RegionExtractor,
               calibrations: list[Calibration] | None = None):
    cals = calibrations or [Calibration("default", thresholds_path, deep_thresholds_path)]
    app = FastAPI(title="LUMEN verification link")
    registry = Registry()
    finished: dict[str, str] = {}  # session id -> record path

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/")
    def page():
        return FileResponse(PAGE_DIR / "index.html")

    @app.get("/static/{name}")
    def static(name: str):
        allowed = {"verify.js", "probe.js", "verify.css"}
        if name not in allowed:
            raise HTTPException(404)
        return FileResponse(PAGE_DIR / name)

    @app.post("/api/sessions")
    async def start(request: Request):
        body = await request.json() if await request.body() else {}
        label = "".join(ch for ch in str(body.get("label", "web"))[:40] if ch.isalnum() or ch in "-_") or "web"
        cal = pick_calibration(cals, str(body.get("camera", ""))[:200], _is_remote(request))
        cfg = SessionConfig(source="browser", duration_s=duration_s, amplitude=amplitude, label=label,
                            lock_camera=False, save_video=save_video, out_dir=out_dir,
                            thresholds_path=cal.thresholds, key_path=key_path, max_lag_s=1.0)
        # Near the gamut edge (amplitude 34 at L* 70) some codes' colour peaks leave the sRGB gamut and
        # the probe refuses them rather than clip; draw a fresh random code instead.
        for attempt in range(8):
            try:
                s = WebSession(cfg, thresholds=load_thresholds(cal.thresholds), extractor_factory=extractor_factory)
                break
            except ValueError:
                if attempt == 7:
                    raise HTTPException(500, "could not draw an in-gamut probe; lower the amplitude") from None
        s.calibration = cal
        s.extra_notes.append(f"calibration: {cal.name} ({cal.thresholds}; deep check {cal.deep})")
        try:
            registry.add(s)
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc
        return {**s.page_params(), "calibration": cal.name}

    @app.post("/api/sessions/{sid}/frame", status_code=204)
    async def frame(sid: str, request: Request):
        s = registry.get(sid)
        if s is None:
            raise HTTPException(404, "unknown or finished session")
        data = await request.body()
        if not 0 < len(data) <= MAX_FRAME_BYTES:
            raise HTTPException(413, "frame empty or too large")
        try:
            t = float(request.headers["X-Capture-T"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, "X-Capture-T header (seconds) required") from exc
        if s.received > (s.cfg.warmup_s + s.cfg.duration_s + 30) * 60:
            raise HTTPException(429, "too many frames for one session")
        s.add_frame(t, data)
        return Response(status_code=204)

    @app.post("/api/sessions/{sid}/finish")
    def finish(sid: str, body: dict):
        s = registry.pop(sid)
        if s is None:
            raise HTTPException(404, "unknown or finished session")
        try:
            r = s.finish(float(body["t0"]), list(body.get("drawn_t") or []), body.get("client") or {},
                         aborted=bool(body.get("aborted")))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, f"bad finish request: {exc}") from exc
        deep_line = None
        if r.record_path:
            finished[r.session_id] = r.record_path
            from lumen.deepcheck import DeepThresholds, deep_measures, describe
            from lumen.record import load_record

            from lumen.deepcheck import judge_temporal

            deep_path = s.calibration.deep
            th = DeepThresholds.load(deep_path) if Path(deep_path).exists() else None
            try:
                m = deep_measures(load_record(r.record_path))
                deep_line = describe(m, th)
                tv = judge_temporal(m)
                temporal_line = f"{tv.decision.upper()}: {tv.reason}"
            except Exception as exc:  # noqa: BLE001  a diagnostic must never cost the verdict
                deep_line = temporal_line = f"not measurable ({type(exc).__name__}: {exc})"
        payload = _result_payload(r, deep_line)
        payload["temporal"] = temporal_line if r.record_path else None
        return JSONResponse(payload)

    @app.get("/api/sessions/{sid}/record")
    def record(sid: str):
        path = finished.get(sid)
        if path is None:
            raise HTTPException(404, "no record for this session")
        return FileResponse(path, media_type="application/json", filename=f"{sid}.json")

    neck_extractor = extractor_factory()
    neck_lock = threading.Lock()

    @app.post("/api/check-frame")
    async def check_frame(request: Request):
        """Pre-flight on one camera frame: where the face and neck regions fall, and whether the neck
        region is on skin. Measured 2026-10-07: sitting closer put the neck region on a dark collar
        (face/neck lightness 1.6-2.0 instead of ~1.0), and every session then abstained at the lamp
        gate; the page shows these outlines so the subject can fix it before starting."""
        data = await request.body()
        if not 0 < len(data) <= MAX_FRAME_BYTES:
            raise HTTPException(413, "frame empty or too large")
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "not an image")
        cal = pick_calibration(cals, request.headers.get("X-Camera", "")[:200], _is_remote(request))
        return {**check_neck(img, neck_extractor, neck_lock, load_thresholds(cal.thresholds)),
                "calibration": cal.name}

    @app.get("/api/info")
    def info():
        first = cals[0]
        return {"calibrated": not load_thresholds(first.thresholds).provisional, "thresholds": first.thresholds,
                "deep_calibrated": Path(first.deep).exists(), "duration_s": duration_s,
                "calibrations": [{"name": c.name, "camera": c.camera, "remote": c.remote,
                                  "calibrated": not load_thresholds(c.thresholds).provisional} for c in cals]}

    return app


# --- HTTPS for other laptops: a self-signed certificate --------------------------------------

def local_ips() -> list[str]:
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        ips.update(i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def ensure_certificate(cert_path: Path, key_path: Path, ips: list[str]) -> None:
    """A self-signed certificate for localhost and this laptop's IPs (regenerated if they change)."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    wanted = sorted(set(ips) | {"127.0.0.1"})
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = sorted(str(ip) for ip in san.get_values_for_type(x509.IPAddress))
        except x509.ExtensionNotFound:
            have = []
        if have == wanted and cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=1):
            return
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LUMEN verification (self-signed)")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")] +
                                                       [x509.IPAddress(ipaddress.ip_address(i)) for i in wanted]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def time_ordered(record: dict) -> dict:
    """A copy of a record with its returned frames in capture-time order (repeated times dropped).

    Web sessions recorded before the server sorted frames kept them in arrival order; their signed
    files are left untouched and analysed through this copy.
    """
    t = record["returned"]["t"]
    order = sorted(range(len(t)), key=lambda i: t[i])
    keep = [i for n, i in enumerate(order) if n == 0 or t[i] > t[order[n - 1]]]
    if keep == list(range(len(t))):
        return record
    ret = record["returned"]
    out = dict(record)
    out["returned"] = {
        **ret, "t": [t[i] for i in keep],
        "regions": {k: [v[i] for i in keep] for k, v in ret["regions"].items()},
        "pose": [ret["pose"][i] for i in keep] if len(ret.get("pose") or []) == len(t) else ret.get("pose", []),
    }
    return out


def with_pipeline_delay(th: Thresholds) -> Thresholds:
    """Switch on L4 for the link: reject when the video answers the light later than 0.25 s.

    The limit is the one fixed before the 2026-10-07 test (deepcheck.TEMPORAL_LAG_MAX_S), where it
    alone was right on all six new sessions; it is ~1.5x the slowest real camera path measured
    (0.167 s, a genuine face through FaceFusion + OBS with the swap off). Known weakness: the page
    reports its own timestamps, so a modified page could hide a delay (docs/RESULTS.md).
    """
    from dataclasses import replace

    from lumen.deepcheck import TEMPORAL_LAG_MAX_S

    return replace(th, pipeline_lag_max_s=TEMPORAL_LAG_MAX_S)


def calibrate_web(sessions_dir: str | Path = "data/web/sessions", n: int = 3,
                  thresholds_path: str = "data/thresholds-web.json",
                  deep_path: str = "data/web/deep_thresholds.json", tag: str = "genuine",
                  sessions: list[str] | None = None) -> list[str]:
    """Calibrate L1-L3 and the deep check from the n newest web sessions opened with ?label=<tag>.

    The tag must name GENUINE sessions only (e.g. genuine, or ff-genuine for a face-swap path with
    the swap switched off); one calibration per setup, because calibration does not transfer
    between cameras, rooms or people (docs/RESULTS.md).
    """
    from lumen.decide import calibrate
    from lumen.deepcheck import calibrate_deep, deep_measures
    from lumen.record import load_record
    from lumen.session import reanalyse

    if sessions:
        # explicit genuine sessions, e.g. one camera path's sessions out of a mixed tag
        found = [Path(sessions_dir) / s for s in sessions]
        if any("genuine" not in d.name or not (d / "record.json").exists() for d in found):
            raise SystemExit("every session must exist and be a genuine one (name containing 'genuine')")
        if len(found) < 3:
            raise SystemExit("calibrate from at least 3 genuine sessions")
        cameras = {load_record(d / "record.json")["capture"]["source"] for d in found}
        if len(cameras) > 1:
            raise SystemExit(f"one camera path per calibration; these sessions used {sorted(cameras)}")
    else:
        if "genuine" not in tag:
            raise SystemExit("calibrate only from genuine sessions: the tag must contain 'genuine'")
        found = sorted((d for d in Path(sessions_dir).glob(f"*-web-{tag}-*") if (d / "record.json").exists()),
                       reverse=True)[:n]
        if len(found) < n:
            raise SystemExit(f"need {n} sessions opened with ?label={tag}, found {len(found)}")
    recs = [time_ordered(load_record(d / "record.json")) for d in sorted(found)]
    if any(r.get("capture", {}).get("simulated") for r in recs):
        raise SystemExit("refusing to calibrate from simulated sessions")
    feats = [reanalyse(r) for r in recs]
    if any(f is None for f in feats):
        raise SystemExit("a genuine session has too little data to calibrate from; record another")
    th = calibrate(feats, poses=[(r.get("extra") or {}).get("pose_summary") for r in recs])
    th = with_pipeline_delay(th)
    th.save(thresholds_path)
    deep = calibrate_deep([deep_measures(r) for r in recs])
    deep.save(deep_path)
    return [d.name for d in sorted(found)] + [
        f"L1-L3 -> {thresholds_path}: cheek contrast >= {th.cheek_contrast_min}, face/neck >= "
        f"{th.face_to_neck_coherence_min}, gain in {list(th.gain_range)}",
        f"deep check -> {deep_path}: lean >= {deep.contrast_min}, relief >= {deep.relief_min}"]


def report_web(match: str = "", sessions_dir: str | Path = "data/web/sessions",
               thresholds_path: str = "data/thresholds-web.json",
               deep_path: str = "data/web/deep_thresholds.json") -> str:
    """Markdown table: every web session (name containing `match`) re-judged with the given calibration."""
    from lumen.decide import decide, load_thresholds
    from lumen.deepcheck import DeepThresholds, deep_measures, judge
    from lumen.record import load_record
    from lumen.session import reanalyse

    th = load_thresholds(thresholds_path)
    deep = DeepThresholds.load(deep_path) if Path(deep_path).exists() else None
    rows = [f"Calibration: {thresholds_path} (cheek contrast >= {th.cheek_contrast_min}); deep check: "
            + (f"{deep_path} (lean >= {deep.contrast_min}, relief >= {deep.relief_min})" if deep else "none"),
            "", "| Session | Neck coh. (floor) | Cheek contrast | Relief | fps | L1-L3 | Deep check |",
            "|---|---|---|---|---|---|---|"]
    for d in sorted(Path(sessions_dir).glob(f"*{match}*")):
        if not (d / "record.json").exists():
            continue
        rec = time_ordered(load_record(d / "record.json"))
        f, ex = reanalyse(rec), rec.get("extra") or {}
        v = decide(f, th, face_fraction=ex.get("face_fraction", 1.0), pose=ex.get("pose_summary"))
        m = deep_measures(rec)
        dv = judge(m, deep).decision if deep else "-"
        neck = "-" if not f else f"{f.measurement.neck.coherence:.3f} ({f.measurement.noise_floor:.3f})"
        cc = "-" if not f or f.cheek_contrast is None else f"{f.cheek_contrast:+.3f}"
        rel = "-" if not m or m.relief is None else f"{m.relief:.3f}"
        rows.append(f"| {d.name} | {neck} | {cc} | {rel} | {rec['capture'].get('fps_reported', '-')} | "
                    f"{v.decision} | {dv} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    import uvicorn

    p = argparse.ArgumentParser(prog="python -m lumen.web", description="LUMEN verification link server")
    p.add_argument("--port", type=int, default=8443)
    p.add_argument("--http", action="store_true", help="plain HTTP on localhost only (no certificate)")
    p.add_argument("--duration", type=float, default=90.0)
    p.add_argument("--amplitude", type=float, default=34.0)
    p.add_argument("--thresholds", default="data/thresholds-web.json", help="L1-L3 calibration of the web path")
    p.add_argument("--no-video", action="store_true", help="do not save the received video")
    p.add_argument("--deep-thresholds", default="data/web/deep_thresholds.json")
    p.add_argument("--calibrate", action="store_true",
                   help="calibrate L1-L3 and the deep check from the 3 newest ?label=<tag> sessions, then exit")
    p.add_argument("--tag", default="genuine", help="with --calibrate: the genuine tag (e.g. ff-genuine)")
    p.add_argument("--sessions", nargs="+", help="with --calibrate: these genuine session folders instead "
                                                 "(one camera path)")
    p.add_argument("--sessions-dir", default="data/web/sessions")
    p.add_argument("--calibrations", default="data/web/calibrations.json",
                   help="rules picking a calibration per camera path (JSON list, first match wins); "
                        "if the file is missing, --thresholds/--deep-thresholds judge every session")
    p.add_argument("--report", metavar="MATCH", nargs="?", const="",
                   help="re-judge saved web sessions (names containing MATCH) with the calibration, then exit")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if a.calibrate:
        for line in calibrate_web(a.sessions_dir, thresholds_path=a.thresholds, deep_path=a.deep_thresholds,
                                  tag=a.tag, sessions=a.sessions):
            print(line)
        return 0
    if a.report is not None:
        print(report_web(a.report, a.sessions_dir, thresholds_path=a.thresholds, deep_path=a.deep_thresholds))
        return 0

    cals = load_calibrations(a.calibrations) if Path(a.calibrations).exists() else None
    if cals:
        print(f"Calibrations ({a.calibrations}), first match wins:")
        for c in cals:
            where = "another laptop" if c.remote else "this laptop" if c.remote is False else "any laptop"
            print(f"   {c.name}: camera contains '{c.camera or '*'}', {where} -> {c.thresholds}")
    app = create_app(thresholds_path=a.thresholds, deep_thresholds_path=a.deep_thresholds,
                     duration_s=a.duration, amplitude=a.amplitude, save_video=not a.no_video, calibrations=cals)
    if a.http:
        print(f"LUMEN verification link: http://localhost:{a.port}  (this laptop only)")
        serve_forever(app, host="127.0.0.1", port=a.port)
        return 0
    ips = local_ips()
    cert, key = Path("data/web/cert.pem"), Path("data/web/cert-key.pem")
    ensure_certificate(cert, key, ips)
    print("LUMEN verification link (HTTPS, self-signed certificate):")
    print(f"   this laptop:    https://localhost:{a.port}")
    for ip in ips:
        print(f"   other laptops:  https://{ip}:{a.port}")
    print("The first time, Chrome warns 'Your connection is not private': Advanced -> Proceed.")
    print("If Windows asks, allow Python on PRIVATE networks. Ctrl+C stops.")
    time.sleep(0.1)
    serve_forever(app, host="0.0.0.0", port=a.port, ssl_certfile=str(cert), ssl_keyfile=str(key))
    return 0


def serve_forever(app, **config) -> None:
    """Run uvicorn on a selector event loop, restarting it if it ever stops.

    Windows' default proactor loop stops accepting connections for good after WinError 64 ("the
    specified network name is no longer available"), which a phone hotspot that drops for a moment
    causes (measured 2026-10-07: the link stopped loading mid-test). The selector loop logs a failed
    accept and keeps listening.
    """
    import asyncio

    import uvicorn

    while True:
        server = uvicorn.Server(uvicorn.Config(app, loop="none", log_level="warning", **config))
        try:
            asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
        except KeyboardInterrupt:
            return
        except Exception as exc:  # noqa: BLE001  keep the link up through network hiccups
            print(f"server stopped ({type(exc).__name__}: {exc}); restarting in 2 s")
        else:
            if server.should_exit:  # Ctrl+C
                return
        time.sleep(2.0)


if __name__ == "__main__":
    raise SystemExit(main())
