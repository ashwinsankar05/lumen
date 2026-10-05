"""FastAPI service for the operator console: run sessions, stream live traces, serve signed records,
verify records, calibrate.

Everything is local: the console is served from console/ with no CDN, so the demo works with the
network cable pulled out. One session runs at a time; its probe window opens on this machine.

Run: python -m lumen serve   (then open http://127.0.0.1:8000)
"""

from __future__ import annotations

import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lumen import __version__
from lumen.decide import Thresholds, calibrate, load_thresholds
from lumen.record import load_or_create_key, load_record, public_key_hex, verify_record
from lumen.session import Session, SessionConfig, reanalyse

CONSOLE_DIR = Path(__file__).resolve().parents[2] / "console"
CLIENT_FIELDS = {"source", "layout", "duration_s", "amplitude", "lightness", "label", "fullscreen",
                 "save_video", "max_lag_s", "warmup_s"}


class SessionManager:
    def __init__(self, base: SessionConfig, factory: Callable[[SessionConfig], Session]):
        self.base = base
        self.factory = factory
        self.sessions: dict[str, Session] = {}
        self.active: Session | None = None
        self._lock = threading.Lock()

    def start(self, overrides: dict[str, Any]) -> Session:
        with self._lock:
            if self.active is not None and self.active.state not in ("done", "error"):
                raise HTTPException(409, f"session {self.active.id} is still running")
            if unknown := set(overrides) - CLIENT_FIELDS:
                raise HTTPException(422, f"unknown fields: {sorted(unknown)}")
            try:
                session = self.factory(SessionConfig(**{**asdict(self.base), **overrides}))
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc
            self.sessions[session.id] = self.active = session
        threading.Thread(target=session.run, name=f"lumen-session-{session.id}", daemon=True).start()
        return session

    def live(self, session_id: str) -> Session:
        if (s := self.sessions.get(session_id)) is None:
            raise HTTPException(404, "no such live session")
        return s

    def record_path(self, session_id: str) -> Path:
        if "/" in session_id or "\\" in session_id or ".." in session_id:
            raise HTTPException(400, "bad session id")
        return Path(self.base.out_dir) / session_id / "record.json"


def summary(record: dict) -> dict:
    v = record.get("verdict") or {}
    feats = record.get("features") or {}
    m = feats.get("measurement") or {}
    extra = record.get("extra") or {}
    capture = record.get("capture") or {}
    return {
        "session_id": record.get("session_id"),
        "started_at": record.get("started_at"),
        "label": extra.get("label", ""),
        "source": capture.get("source"),
        "simulated": bool(capture.get("simulated")),
        "layout": (record.get("probe") or {}).get("layout"),
        "decision": v.get("decision"),
        "reason": v.get("reason"),
        "provisional": v.get("provisional"),
        "face_coherence": (m.get("face") or {}).get("coherence"),
        "neck_coherence": (m.get("neck") or {}).get("coherence"),
        "differential_gain": m.get("differential_gain"),
        "cheek_contrast": feats.get("cheek_contrast"),
    }


def create_app(base: SessionConfig | None = None,
               session_factory: Callable[[SessionConfig], Session] = Session) -> FastAPI:
    base = base or SessionConfig()
    manager = SessionManager(base, session_factory)
    app = FastAPI(title="LUMEN", version=__version__)
    app.state.manager = manager

    @app.get("/api/health")
    def health():
        th = load_thresholds(base.thresholds_path)
        return {"ok": True, "version": __version__, "thresholds_provisional": th.provisional,
                "public_key": public_key_hex(load_or_create_key(base.key_path))}

    @app.get("/api/config")
    def config():
        return {k: v for k, v in asdict(base).items() if k in CLIENT_FIELDS}

    @app.post("/api/sessions")
    def start(body: dict = Body(default_factory=dict)):
        return {"session_id": manager.start(body).id}

    @app.get("/api/sessions")
    def list_sessions():
        rows = []
        for path in sorted(Path(base.out_dir).glob("*/record.json"), reverse=True):
            try:
                rows.append(summary(load_record(path)))
            except (OSError, ValueError):
                continue
        a = manager.active
        if a is not None and a.state not in ("done", "error"):
            rows.insert(0, {"session_id": a.id, "label": a.cfg.label, "source": a.cfg.source,
                            "decision": None, "state": a.state})
        return rows

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        if (s := manager.sessions.get(session_id)) is not None:
            return {"session_id": s.id, "state": s.state, "result": s.result.to_dict() if s.result else None}
        path = manager.record_path(session_id)
        if not path.exists():
            raise HTTPException(404, "no such session")
        return {"session_id": session_id, "state": "done", "summary": summary(load_record(path))}

    @app.get("/api/sessions/{session_id}/live")
    def live(session_id: str, since: int = 0):
        s = manager.live(session_id)
        snap = s.snapshot(since)
        if s.result is not None:
            snap["result"] = s.result.to_dict()
        return snap

    @app.post("/api/sessions/{session_id}/stop")
    def stop(session_id: str):
        manager.live(session_id).stop()
        return {"stopping": True}

    @app.get("/api/sessions/{session_id}/record")
    def record(session_id: str):
        path = manager.record_path(session_id)
        if not path.exists():
            raise HTTPException(404, "no record for this session")
        return FileResponse(path, media_type="application/json", filename=f"lumen-{session_id}.json")

    @app.post("/api/records/verify")
    def verify(record: dict = Body(...)):
        check = verify_record(record, expected_public_key=public_key_hex(load_or_create_key(base.key_path)))
        return {"ok": check.ok, **asdict(check)}

    @app.get("/api/thresholds")
    def thresholds():
        return load_thresholds(base.thresholds_path).to_dict()

    @app.post("/api/calibrate")
    def calibrate_from(body: dict = Body(...)):
        ids = body.get("session_ids") or []
        if not ids:
            raise HTTPException(422, "give the ids of genuine sessions recorded in today's conditions")
        feats, poses = [], []
        for sid in ids:
            path = manager.record_path(sid)
            if not path.exists():
                raise HTTPException(404, f"no record for {sid}")
            rec = load_record(path)
            if (f := reanalyse(rec)) is None:
                raise HTTPException(422, f"session {sid} has too little data to calibrate from")
            feats.append(f)
            poses.append((rec.get("extra") or {}).get("pose_summary"))
        th = calibrate(feats, poses=poses)
        th.save(base.thresholds_path)
        return th.to_dict()

    @app.delete("/api/thresholds")
    def reset_thresholds():
        Path(base.thresholds_path).unlink(missing_ok=True)
        return Thresholds().to_dict()

    @app.exception_handler(ValueError)
    def value_error(_, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    if CONSOLE_DIR.exists():
        app.mount("/static", StaticFiles(directory=CONSOLE_DIR), name="static")

        @app.get("/")
        def index():
            return FileResponse(CONSOLE_DIR / "index.html")

    return app
