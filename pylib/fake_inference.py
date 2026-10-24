#!/usr/bin/env python3
"""OpenAI-compatible stub for characterization: GET /v1/models, POST /v1/chat/completions (stream or not),
POST /v1/embeddings. No inference."""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state", required=True)          # JSON re-read per request: {"chunks": 3, "delay": 0.05, "mode": "ok"|"fail"}
    args = ap.parse_args()

    def state() -> dict:
        try:
            return json.load(open(args.state))
        except (OSError, ValueError):
            return {}

    class H(BaseHTTPRequestHandler):                   # HTTP/1.0: the stream is delimited by closing the connection
        def _json(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] == "/v1/models":
                return self._json(200, {"object": "list", "data": [{"id": "fake-model", "object": "model", "owned_by": "fixture", "context_length": 8192}]})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            s = state()
            if s.get("mode") == "fail":
                return self._json(500, {"error": {"message": "fixture failure"}})
            chunks, delay = int(s.get("chunks", 3)), float(s.get("delay", 0.05))
            if self.path.split("?")[0] == "/v1/embeddings":
                return self._json(200, {"object": "list", "model": req.get("model", ""), "usage": {"prompt_tokens": 1, "total_tokens": 1},
                                        "data": [{"object": "embedding", "index": 0, "embedding": [0.0, 0.1, 0.2]}]})
            if not req.get("stream"):
                time.sleep(chunks * delay)
                return self._json(200, {"id": "c", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": "x" * chunks}, "finish_reason": "stop"}]})
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            try:
                for _ in range(chunks):
                    self.wfile.write(("data: " + json.dumps({"id": "c", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "x"}}]}) + "\n\n").encode())
                    self.wfile.flush(); time.sleep(delay)
                self.wfile.write(b"data: [DONE]\n\n")
            except OSError:
                pass

        def log_message(self, *a):
            pass

    class Server(ThreadingHTTPServer):
        request_queue_size = 256                       # the default backlog of 5 drops SYNs under concurrent load

    Server((args.host, args.port), H).serve_forever()


if __name__ == "__main__":
    main()
