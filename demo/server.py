"""BrightPath support AI demo server (Python standard library only).

    python server.py            ->  http://localhost:8000
    python server.py 9000       ->  http://localhost:9000
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import api
import llm
from api import STORE

STATIC_DIR = (Path(__file__).parent / "static").resolve()


class Handler(BaseHTTPRequestHandler):
    server_version = "BrightPathDemo/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status: int, body: str) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, json.dumps({"error": message}))

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            return self._error(404, "Not found")
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "text/javascript"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        self._send_json(*api.handle("GET", self.path))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except json.JSONDecodeError:
            return self._error(400, "Body must be JSON")
        self._send_json(*api.handle("POST", self.path, data))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    # On Windows SO_REUSEADDR lets a second server bind a busy port silently; fail loudly instead.
    ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        sys.exit(f"Port {port} is already in use. Stop the other server or run: python server.py <other-port>")
    print(f"BrightPath support AI demo running at http://localhost:{port}", flush=True)
    print(f"Intent engine: {llm.engine_label()}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
