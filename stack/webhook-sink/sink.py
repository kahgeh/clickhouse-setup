#!/usr/bin/env python3
"""Tiny webhook receiver for alert verification (standard library only).

  POST /hook        records {received_at, path, content_type, body} (body kept as raw text)
  GET  /received    -> JSON list of everything recorded, oldest first
  DELETE /received  -> clears the list
  GET  /healthz     -> ok
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
RECEIVED = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        with LOCK:
            RECEIVED.append({"received_at": time.time(), "path": self.path,
                             "content_type": self.headers.get("Content-Type", ""), "body": body})
        self._send(200, {"ok": True})

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        if self.path == "/received":
            with LOCK:
                return self._send(200, list(RECEIVED))
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        with LOCK:
            RECEIVED.clear()
        self._send(200, {"ok": True})

    def log_message(self, fmt, *args):  # keep container logs quiet except for hook deliveries
        if self.command == "POST":
            print(f"hook {self.path} {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8089), Handler).serve_forever()
