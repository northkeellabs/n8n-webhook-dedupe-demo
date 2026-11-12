"""Mock OpenAI-compatible chat completions server (Python stdlib only).

Used by the demo so nothing needs a paid key. Every response is canned text.

Endpoints
  POST /v1/chat/completions  OpenAI-compatible; waits MOCK_LATENCY_S then answers.
                             The event id is read from the X-Event-Id request header
                             (falls back to the "user" field of the body, then "unknown").
  GET  /stats                call counts, total and per event id (ok / failed).
  POST /reset                clear counters; optional JSON body
                             {"latency_s": 1.5, "fail_first": ["evt-1", ...]}
  GET  /health               liveness.

Fault injection: for every event id in fail_first (or the MOCK_FAIL_FIRST env var,
comma separated) the FIRST call for that id returns HTTP 500; later calls succeed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_state: dict = {}


def _reset(latency_s: float | None = None, fail_first: list[str] | None = None) -> None:
    with _lock:
        _state["latency_s"] = (
            float(latency_s) if latency_s is not None else float(os.environ.get("MOCK_LATENCY_S", "1.5"))
        )
        env_fail = [x for x in os.environ.get("MOCK_FAIL_FIRST", "").split(",") if x]
        _state["fail_first"] = set(fail_first if fail_first is not None else env_fail)
        _state["already_failed"] = set()
        _state["by_event"] = {}
        _state["total"] = 0
        _state["ok"] = 0
        _state["failed"] = 0


_reset()


class Handler(BaseHTTPRequestHandler):
    server_version = "mock-llm/1.0"

    def log_message(self, fmt, *args):  # keep container logs quiet
        pass

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/stats":
            with _lock:
                return self._send(
                    200,
                    {
                        "total_calls": _state["total"],
                        "ok_calls": _state["ok"],
                        "failed_calls": _state["failed"],
                        "latency_s": _state["latency_s"],
                        "by_event": _state["by_event"],
                    },
                )
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/reset":
            body = self._read_json()
            _reset(body.get("latency_s"), body.get("fail_first"))
            return self._send(200, {"status": "reset", "latency_s": _state["latency_s"]})
        if self.path.rstrip("/") == "/v1/chat/completions":
            return self._chat()
        return self._send(404, {"error": "not found"})

    def _chat(self):
        body = self._read_json()
        event_id = self.headers.get("X-Event-Id") or body.get("user") or "unknown"
        with _lock:
            latency = _state["latency_s"]
            fail = event_id in _state["fail_first"] and event_id not in _state["already_failed"]
            if fail:
                _state["already_failed"].add(event_id)
        time.sleep(latency)
        with _lock:
            _state["total"] += 1
            counts = _state["by_event"].setdefault(event_id, {"ok": 0, "failed": 0})
            if fail:
                _state["failed"] += 1
                counts["failed"] += 1
            else:
                _state["ok"] += 1
                counts["ok"] += 1
        if fail:
            return self._send(500, {"error": {"message": "injected fault (mock)", "type": "server_error"}})
        model = body.get("model") or "mock-model"
        return self._send(
            200,
            {
                "id": f"chatcmpl-mock-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"Mock summary for fictional event {event_id}."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            },
        )


def main() -> None:
    port = int(os.environ.get("MOCK_PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    print(f"mock-llm listening on :{port}, latency {_state['latency_s']} s", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
