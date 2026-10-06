"""Relay: send each finished session from a remote laptop to this one, automatically.

LAB / DATA-COLLECTION MODE ONLY. The sending laptop runs the probe and does the measuring, so
whoever controls it could send made-up values: this is for collecting data with a cooperating
teammate (e.g. a face-swap test on the attacker laptop), never for verifying a stranger. In a
real deployment the measuring happens on the verifier's side (call mode, lumen-call.cmd).

    this laptop:    python -m lumen.relay serve            prints the address and a token
    other laptop:   python -m lumen.deepcheck live ... --send-to http://<address>:8765 --token <token>
                    python -m lumen.relay send --to http://<address>:8765 --token <token> --latest 3

What the receiver does with each record: checks the token, the Ed25519 signature and that the
emission log regenerates from the nonce; pins the sender's public key on the first record and
refuses records signed by any other key; saves it under data/remote/<session id>/; runs the deep
check; and appends a line to data/remote/results.md. Sessions whose name contains "genuine"
are calibration data: once three have arrived, the deep check calibrates from them (genuine
only, PROJECT.md §6) and judges every later session. Built on the standard library only.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lumen.deepcheck import DeepThresholds, calibrate_deep, deep_measures, describe
from lumen.record import save_record, verify_record

DEFAULT_PORT = 8765
MAX_BYTES = 40 * 1024 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def local_addresses() -> list[str]:
    """This laptop's IPv4 addresses on the local network (what the other laptop should use)."""
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outgoing interface
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


class Receiver:
    """State of the receiving side: where records go, the pinned key, the deep-check calibration."""

    def __init__(self, out_dir: str | Path = "data/remote", token: str | None = None, n_calibration: int = 3):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.token = token or secrets.token_urlsafe(12)
        self.n_calibration = n_calibration
        self.key_file = self.out_dir / "sender_public_key.txt"
        self.th_file = self.out_dir / "deep_thresholds.json"
        self.results = self.out_dir / "results.md"
        self.lock = threading.Lock()

    # -- one record ------------------------------------------------------------------------------
    def accept(self, record: dict) -> tuple[int, dict]:
        """(HTTP status, reply) for one received record."""
        sid = str(record.get("session_id", ""))
        if not _SAFE_ID.match(sid):
            return 400, {"error": "missing or unsafe session_id"}
        with self.lock:
            pinned = self.key_file.read_text().strip() if self.key_file.exists() else None
            check = verify_record(record, expected_public_key=pinned)
            if not check.ok:
                return 422, {"error": "record refused", "problems": check.problems}
            if pinned is None:
                self.key_file.write_text(record["signature"]["public_key"])
            save_record(record, self.out_dir / sid / "record.json")

            m = deep_measures(record)
            genuine = "genuine" in sid
            th = DeepThresholds.load(self.th_file) if self.th_file.exists() else None
            note = ""
            if th is None and genuine:
                pool = self._genuine_sessions()
                if len(pool) >= self.n_calibration:
                    measures = [deep_measures(json.loads((d / "record.json").read_text()))
                                for d in pool[: self.n_calibration]]
                    try:
                        th = calibrate_deep(measures)
                        th.save(self.th_file)
                        note = f"calibrated from {th.n_genuine} genuine sessions"
                    except ValueError as exc:
                        note = f"calibration failed: {exc}"
                else:
                    note = f"genuine calibration session {len(pool)} of {self.n_calibration}"
            line = describe(m, th)
            sender = record.get("verdict", {})
            row = (f"| {datetime.now():%H:%M:%S} | {sid} | {sender.get('decision', '?')} | {line}"
                   f"{' | ' + note if note else ' |'}")
            if not self.results.exists():
                self.results.write_text("| Received | Session | L1-L3 (sender) | Deep check | Note |\n"
                                        "|---|---|---|---|---|\n", encoding="utf-8")
            with self.results.open("a", encoding="utf-8") as f:
                f.write(row + "\n")
        print(f"\n[{datetime.now():%H:%M:%S}] {sid}  (signature valid)")
        print(f"   L1-L3 verdict on the sender: {sender.get('decision', '?').upper()}: {sender.get('reason', '')}")
        print(f"   deep check: {line}")
        if note:
            print(f"   {note}")
        return 200, {"session_id": sid, "l1_l3": sender.get("decision"), "deep": line, "note": note}

    def _genuine_sessions(self) -> list[Path]:
        return sorted(d for d in self.out_dir.iterdir()
                      if d.is_dir() and "genuine" in d.name and (d / "record.json").exists())


def make_handler(receiver: Receiver):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            return hmac.compare_digest(self.headers.get("X-Lumen-Token", ""), receiver.token)

        def do_GET(self):  # noqa: N802
            if self.path != "/ping":
                return self._reply(404, {"error": "not found"})
            if not self._authorised():
                return self._reply(401, {"error": "wrong token"})
            return self._reply(200, {"ok": True})

        def do_POST(self):  # noqa: N802
            if self.path != "/record":
                return self._reply(404, {"error": "not found"})
            if not self._authorised():
                return self._reply(401, {"error": "wrong token"})
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_BYTES:
                return self._reply(413, {"error": "empty or too large"})
            try:
                record = json.loads(self.rfile.read(n))
            except ValueError:
                return self._reply(400, {"error": "not JSON"})
            if not isinstance(record, dict):
                return self._reply(400, {"error": "not a record"})
            try:
                status, body = receiver.accept(record)
            except Exception as exc:  # noqa: BLE001  the server must survive one bad record
                status, body = 500, {"error": f"{type(exc).__name__}: {exc}"}
            return self._reply(status, body)

        def log_message(self, fmt, *args):  # quiet: accept() prints what matters
            pass

    return Handler


def serve(port: int = DEFAULT_PORT, out_dir: str | Path = "data/remote", token: str | None = None) -> None:
    receiver = Receiver(out_dir, token)
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(receiver))
    print("LUMEN relay receiver (lab mode: the sending laptop does the measuring)")
    for a in local_addresses() or ["<this laptop's IP>"]:
        print(f"   address: http://{a}:{port}")
    print(f"   token:   {receiver.token}")
    print(f"   saving to {Path(out_dir).resolve()}; results in {receiver.results}")
    print("If Windows asks, allow Python on PRIVATE networks. Ctrl+C stops.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _request(url: str, token: str, data: bytes | None = None, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"X-Lumen-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except ValueError:
            body = {}
        return {"error": body.get("error", f"HTTP {exc.code}"), **{k: v for k, v in body.items() if k != "error"}}
    except (urllib.error.URLError, OSError) as exc:
        return {"error": f"cannot reach {url}: {exc}"}


def ping(base_url: str, token: str) -> dict:
    return _request(base_url.rstrip("/") + "/ping", token, timeout=5.0)


def send_record(base_url: str, token: str, record_path: str | Path) -> dict:
    p = Path(record_path)
    p = p / "record.json" if p.is_dir() else p
    return _request(base_url.rstrip("/") + "/record", token, p.read_bytes())


def main(argv: list[str] | None = None) -> int:
    import argparse

    from lumen.deepcheck import latest_sessions

    p = argparse.ArgumentParser(prog="python -m lumen.relay", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="receive sessions on this laptop")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--out", default="data/remote")
    s.add_argument("--token", help="reuse a token (default: a new random one)")
    t = sub.add_parser("send", help="send saved sessions to a receiver")
    t.add_argument("records", nargs="*")
    t.add_argument("--to", required=True, help="e.g. http://192.168.1.20:8765")
    t.add_argument("--token", required=True)
    t.add_argument("--latest", type=int)
    t.add_argument("--label", default="")
    k = sub.add_parser("ping", help="check that the receiver is reachable and the token is right")
    k.add_argument("--to", required=True)
    k.add_argument("--token", required=True)
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # arrivals show at once

    if a.cmd == "serve":
        serve(a.port, a.out, a.token)
        return 0
    if a.cmd == "ping":
        r = ping(a.to, a.token)
        print("receiver reachable, token accepted" if r.get("ok") else f"FAILED: {r.get('error')}")
        return 0 if r.get("ok") else 1
    paths = [Path(x) for x in a.records] + (latest_sessions(a.latest, a.label) if a.latest else [])
    if not paths:
        print("nothing to send")
        return 1
    failed = 0
    for x in paths:
        r = send_record(a.to, a.token, x)
        if "error" in r:
            failed += 1
            print(f"{x.name}: NOT sent: {r['error']} {r.get('problems', '')}")
        else:
            print(f"{x.name}: sent. Receiver's deep check: {r['deep']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
