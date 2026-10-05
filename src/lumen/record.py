"""Signed session record: what was emitted, what returned, what was measured and decided.

The record holds the one-time code (revealed once the session is over), the probe parameters, the
full emission log, the per-frame region colours, the capture report, the features, the
thresholds and the verdict. It is signed with Ed25519 over canonical JSON (sorted keys, no
whitespace, no NaN).

`verify_record` checks the signature and then regenerates the probe from the nonce and compares
the emission log with it, so a valid record proves that what was emitted is exactly the sequence
that code defines. Features can be recomputed from the stored series (session.reanalyse).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from lumen import __version__
from lumen.probe import EmissionLog, Probe

SCHEMA = "lumen.session/1"
EMISSION_TOLERANCE = 1e-6  # CIELAB units; the log stores full float precision


def load_or_create_key(path: str | Path) -> Ed25519PrivateKey:
    path = Path(path)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 key")
        return key
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    return key


def public_key_hex(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _rows(values) -> list:
    return [None if v is None else [round(float(x), 4) for x in np.ravel(v)] for v in values]


def build_record(
    *,
    session_id: str,
    started_at: str,
    nonce: str,
    log: EmissionLog,
    capture_t,
    regions: dict[str, list],
    pose: list | None = None,
    capture: dict,
    features: dict | None,
    verdict: dict,
    thresholds: dict,
    extra: dict | None = None,
) -> dict:
    """Unsigned record.

    regions: name -> one entry per captured frame in capture_t, each an (L*, a*, b*) row or None
    where that region was not found. pose: per frame (yaw, roll_deg, ipd_px) or None.
    """
    return {
        "schema": SCHEMA,
        "software": f"lumen {__version__}",
        "session_id": session_id,
        "started_at": started_at,
        "probe": {**log.probe.params(), "nonce": nonce},
        "emitted": {"t0": log.t0, "t": log.t.tolist(), "lab": log.lab.tolist()},
        "returned": {
            "t": np.asarray(capture_t, dtype=float).tolist(),
            "regions": {name: _rows(rows) for name, rows in regions.items()},
            "pose": _rows(pose or []),
        },
        "capture": capture,
        "features": features,
        "thresholds": thresholds,
        "verdict": verdict,
        "extra": extra or {},
    }


def sign_record(record: dict, key: Ed25519PrivateKey) -> dict:
    body = {k: v for k, v in record.items() if k != "signature"}
    return {**body, "signature": {"alg": "ed25519", "public_key": public_key_hex(key),
                                  "value": key.sign(canonical(body)).hex()}}


@dataclass
class RecordCheck:
    signature_ok: bool
    emission_matches_nonce: bool
    trusted_key: bool | None  # None when no expected key was given
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.signature_ok and self.emission_matches_nonce and self.trusted_key is not False


def verify_record(record: dict, expected_public_key: str | None = None) -> RecordCheck:
    problems: list[str] = []
    sig = record.get("signature") or {}
    body = {k: v for k, v in record.items() if k != "signature"}

    signature_ok = False
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(sig["public_key"])).verify(
            bytes.fromhex(sig["value"]), canonical(body))
        signature_ok = True
    except (KeyError, ValueError, TypeError, InvalidSignature) as exc:
        problems.append(f"signature invalid ({type(exc).__name__})")

    trusted = None
    if expected_public_key is not None:
        trusted = sig.get("public_key") == expected_public_key
        if not trusted:
            problems.append("signed by an unexpected key")

    emission_ok = False
    try:
        probe = Probe.from_params(record["probe"]["nonce"], record["probe"])
        t = np.asarray(record["emitted"]["t"], dtype=float) - record["emitted"]["t0"]
        lab = np.asarray(record["emitted"]["lab"], dtype=float)
        if len(t) == 0:
            problems.append("empty emission log")
        else:
            expected = probe.lab_at(t)
            if expected.shape != lab.shape:
                raise ValueError(f"emission log shape {lab.shape} does not match the {probe.layout} layout")
            err = float(np.abs(expected - lab).max())
            emission_ok = err < EMISSION_TOLERANCE
            if not emission_ok:
                problems.append(f"emission log does not match the nonce's probe (max error {err:.3g})")
    except (KeyError, ValueError, TypeError) as exc:
        problems.append(f"cannot regenerate the probe: {exc}")

    return RecordCheck(signature_ok, emission_ok, trusted, problems)


def save_record(record: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True, allow_nan=False))
    return path


def load_record(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
