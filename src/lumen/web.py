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


def create_app(*, out_dir: str = "data/web/sessions", thresholds_path: str = "data/thresholds-web.json",
               deep_thresholds_path: str = "data/web/deep_thresholds.json",
               key_path: str = "data/keys/lumen_ed25519.pem", duration_s: float = 90.0,
               amplitude: float = 34.0, save_video: bool = True, extractor_factory=RegionExtractor):
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
        cfg = SessionConfig(source="browser", duration_s=duration_s, amplitude=amplitude, label=label,
                            lock_camera=False, save_video=save_video, out_dir=out_dir,
                            thresholds_path=thresholds_path, key_path=key_path, max_lag_s=1.0)
        # Near the gamut edge (amplitude 34 at L* 70) some codes' colour peaks leave the sRGB gamut and
        # the probe refuses them rather than clip; draw a fresh random code instead.
        for attempt in range(8):
            try:
                s = WebSession(cfg, thresholds=load_thresholds(thresholds_path), extractor_factory=extractor_factory)
                break
            except ValueError:
                if attempt == 7:
                    raise HTTPException(500, "could not draw an in-gamut probe; lower the amplitude") from None
        try:
            registry.add(s)
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc
        return s.page_params()

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

            th = DeepThresholds.load(deep_thresholds_path) if Path(deep_thresholds_path).exists() else None
            deep_line = describe(deep_measures(load_record(r.record_path)), th)
        return JSONResponse(_result_payload(r, deep_line))

    @app.get("/api/sessions/{sid}/record")
    def record(sid: str):
        path = finished.get(sid)
        if path is None:
            raise HTTPException(404, "no record for this session")
        return FileResponse(path, media_type="application/json", filename=f"{sid}.json")

    @app.get("/api/info")
    def info():
        th = load_thresholds(thresholds_path)
        return {"calibrated": not th.provisional, "thresholds": thresholds_path,
                "deep_calibrated": Path(deep_thresholds_path).exists(), "duration_s": duration_s}

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


def calibrate_web(sessions_dir: str | Path = "data/web/sessions", n: int = 3,
                  thresholds_path: str = "data/thresholds-web.json",
                  deep_path: str = "data/web/deep_thresholds.json", tag: str = "genuine") -> list[str]:
    """Calibrate L1-L3 and the deep check from the n newest web sessions opened with ?label=<tag>.

    The tag must name GENUINE sessions only (e.g. genuine, or ff-genuine for a face-swap path with
    the swap switched off); one calibration per setup, because calibration does not transfer
    between cameras, rooms or people (docs/RESULTS.md).
    """
    from lumen.decide import calibrate
    from lumen.deepcheck import calibrate_deep, deep_measures
    from lumen.record import load_record
    from lumen.session import reanalyse

    if "genuine" not in tag:
        raise SystemExit("calibrate only from genuine sessions: the tag must contain 'genuine'")
    found = sorted((d for d in Path(sessions_dir).glob(f"*-web-{tag}-*") if (d / "record.json").exists()),
                   reverse=True)[:n]
    if len(found) < n:
        raise SystemExit(f"need {n} sessions opened with ?label={tag}, found {len(found)}")
    recs = [load_record(d / "record.json") for d in sorted(found)]
    if any(r.get("capture", {}).get("simulated") for r in recs):
        raise SystemExit("refusing to calibrate from simulated sessions")
    feats = [reanalyse(r) for r in recs]
    if any(f is None for f in feats):
        raise SystemExit("a genuine session has too little data to calibrate from; record another")
    th = calibrate(feats, poses=[(r.get("extra") or {}).get("pose_summary") for r in recs])
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
        rec = load_record(d / "record.json")
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
    p.add_argument("--report", metavar="MATCH", nargs="?", const="",
                   help="re-judge saved web sessions (names containing MATCH) with the calibration, then exit")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if a.calibrate:
        for line in calibrate_web(thresholds_path=a.thresholds, deep_path=a.deep_thresholds, tag=a.tag):
            print(line)
        return 0
    if a.report is not None:
        print(report_web(a.report, thresholds_path=a.thresholds, deep_path=a.deep_thresholds))
        return 0

    app = create_app(thresholds_path=a.thresholds, deep_thresholds_path=a.deep_thresholds,
                     duration_s=a.duration, amplitude=a.amplitude, save_video=not a.no_video)
    if a.http:
        print(f"LUMEN verification link: http://localhost:{a.port}  (this laptop only)")
        uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
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
    uvicorn.run(app, host="0.0.0.0", port=a.port, ssl_certfile=str(cert), ssl_keyfile=str(key),
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
