#!/usr/bin/env python3
"""Strict loopback webhook sink for local incident notification proofs."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class LoopbackNotificationSink:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._lock = threading.Lock()

    def record(self, path: str, body: bytes, status_code: int) -> None:
        with self._lock:
            self.requests.append(
                {
                    "path": path,
                    "body": body.decode("utf-8", errors="replace")[:4000],
                    "status_code": status_code,
                }
            )

    def count(self) -> int:
        with self._lock:
            return len(self.requests)

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()


SINK = LoopbackNotificationSink()


class Handler(BaseHTTPRequestHandler):
    fail_next = False

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b""
        if Handler.fail_next:
            Handler.fail_next = False
            status = 503
            payload = {"ok": False, "reason": "forced_failure"}
        else:
            status = 200
            payload = {"ok": True}
        SINK.record(self.path, body, status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))


def serve(host: str = "127.0.0.1", port: int = 18765) -> HTTPServer:
    server = HTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == "__main__":
    import time

    srv = serve()
    print(json.dumps({"ok": True, "url": f"http://127.0.0.1:18765/mock"}), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
