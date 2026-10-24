#!/usr/bin/env python3
"""Fake OmniRoute admin API (mechanics only; shapes mirror the fixture profile's api_fields)."""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")  # the Task 8 proof image passes 0.0.0.0 so the published port is reachable
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state", required=True)  # JSON: {"active":0,"waiting":0,"by_model":{},"rows":[],"cache_ms":1000}
    args = ap.parse_args()

    def state() -> dict:
        try:
            with open(args.state) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    snapshot: dict = {}
    lock = threading.Lock()

    def health(s: dict) -> dict:
        """Stale-while-revalidate, as the real endpoint (measured 2026-10-24): answer the previous snapshot and, once
        it is older than the cache window, recompute behind it, so a lone read's age is the gap since the last read."""
        now = int(time.time() * 1000)
        fresh = {"timestamp": now, "chatAdmission": {"activeHeavy": 0, "activeHealthyHeadroom": 0,
                                                      "waiting": s.get("waiting", 0), "queuedBytes": 0}}
        with lock:
            previous = snapshot.get("body", fresh)
            if previous is fresh or now - previous["timestamp"] >= int(s.get("cache_ms", 1000)):
                snapshot["body"] = fresh
        return previous

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, headers=()):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path == "/api/auth/login":
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                print(json.dumps({"method": "POST", "path": self.path}), flush=True)
                return self._send(200, {"ok": True}, [("Set-Cookie", "auth_token=fixture; Path=/")])
            self._send(404, {"error": "not found"})

        def do_GET(self):
            s = state()
            if self.path.startswith("/api/monitoring/health"):
                return self._send(200, health(s))
            if self.path.startswith("/api/usage/history"):
                by_model = s.get("by_model", {})
                return self._send(200, {"active": s.get("active", 0),
                                        "pending": {"byModel": by_model,
                                                    "byAccount": {"a": sum(by_model.values())} if by_model else {}}})
            if self.path.startswith("/api/usage/call-logs"):
                return self._send(200, {"logs": s.get("rows", [])})
            self._send(404, {"error": "not found"})

        def log_message(self, *a):  # stdout carries only the JSON access lines above
            pass

    print(json.dumps({"event": "started"}), flush=True)  # a journal seed record for the observer (no method/path: ignored)
    ThreadingHTTPServer((args.host, args.port), H).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
