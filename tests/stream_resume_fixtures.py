#!/usr/bin/env python3
"""Exercise native provider SSE decoding and replayable output chunks."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ProviderHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        events = [
            b": keepalive\n\n",
            b"id: provider-1\n",
            b"event: message\n",
            'data: {"id":"chat-1","choices":[{"delta":{"content":"第一"},"finish_reason":null}]}\n\n'.encode(),
            # Two data lines in one SSE event validate multi-line JSON handling.
            b"id: provider-2\n",
            'data: {"choices":[{"delta":\n'.encode(),
            'data: {"content":"段输出"},"finish_reason":null}]}\n\n'.encode(),
            b"data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
            b"data: [DONE]\n\n",
        ]
        for event in events:
            self.wfile.write(event)
            self.wfile.flush()

    def log_message(self, *_args: object) -> None:
        return


def request(base: str, method: str, path: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict | str]:
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request_obj = urllib.request.Request(base + path, data=body, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request_obj, timeout=8) as response:
        raw = response.read().decode()
        content_type = response.headers.get_content_type()
        return response.status, raw if content_type == "text/event-stream" else json.loads(raw)


def main() -> None:
    provider = ThreadingHTTPServer(("127.0.0.1", port()), ProviderHandler)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    app_port = port()
    with tempfile.TemporaryDirectory(prefix="moyu-stream-") as tmp:
        env = {
            **os.environ,
            "MOYU_DB": str(Path(tmp) / "stream.sqlite3"),
            "MOYU_HOST": "127.0.0.1",
            "MOYU_PORT": str(app_port),
            "MOYU_AI_BASE_URL": f"http://127.0.0.1:{provider.server_address[1]}",
            "MOYU_AI_MODEL": "fixture-model",
            "MOYU_AI_API_KEY": "",
        }
        process = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{app_port}"
        try:
            for _ in range(60):
                try:
                    request(base, "GET", "/api/health")
                    break
                except Exception:
                    time.sleep(0.05)
            status, raw = request(base, "POST", "/api/ai", {"task": "continue", "stream": True})
            assert status == 200 and isinstance(raw, str)
            assert '"streaming": "provider_native_sse"' in raw
            assert "event: delta" in raw and "id: 1" in raw and "id: 2" in raw and "event: done" in raw
            run_id = json.loads(next(line[6:] for line in raw.splitlines() if line.startswith("data: {") and '"ai_run_id"' in line))["ai_run_id"]
            replay_status, replay = request(base, "GET", f"/api/ai/runs/{run_id}/stream?after_seq=1")
            assert replay_status == 200 and isinstance(replay, str)
            assert '"seq": 1' not in replay and '"seq": 2' in replay and "event: done" in replay
            detail_status, detail = request(base, "GET", f"/api/ai/runs/{run_id}")
            assert detail_status == 200 and isinstance(detail, dict)
            assert detail["stream"]["chunk_count"] == 2 and detail["stream"]["last_seq"] == 2
        finally:
            process.terminate()
            process.wait(timeout=5)
    provider.shutdown()
    provider.server_close()
    print("stream resume fixtures: ok")


if __name__ == "__main__":
    main()
