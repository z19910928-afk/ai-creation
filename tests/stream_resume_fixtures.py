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
    finished = threading.Event()
    first_chunk_observed = threading.Event()
    observation_timed_out = threading.Event()
    payload: dict = {}

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        type(self).payload = json.loads(self.rfile.read(length))
        type(self).finished.clear()
        type(self).first_chunk_observed.clear()
        type(self).observation_timed_out.clear()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        events = [
            b": keepalive\n\n",
            b"id: provider-1\n",
            b"event: message\n",
            'data: {"id":"chat-1","model":"fixture-model","choices":[{"index":0,"delta":{"content":"第一"},"finish_reason":null}]}\n\n'.encode(),
            # Two data lines in one SSE event validate multi-line JSON handling.
            b"id: provider-2\n",
            'data: {"choices":[{"index":0,"delta":\n'.encode(),
            'data: {"content":"段输出"},"finish_reason":null}]}\n\n'.encode(),
            b"data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
            b'data: {"choices":[],"usage":{"prompt_tokens":41,"completion_tokens":8,"total_tokens":49}}\n\n',
            b"data: [DONE]\n\n",
        ]
        for position, event in enumerate(events):
            self.wfile.write(event)
            self.wfile.flush()
            if position == 3:
                if not type(self).first_chunk_observed.wait(timeout=5):
                    type(self).observation_timed_out.set()
        type(self).finished.set()

    def log_message(self, *_args: object) -> None:
        return


def request(base: str, method: str, path: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict | str]:
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request_obj = urllib.request.Request(base + path, data=body, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request_obj, timeout=8) as response:
        raw = response.read().decode()
        content_type = response.headers.get_content_type()
        return response.status, raw if content_type == "text/event-stream" else json.loads(raw)


def live_generation(base: str) -> list[dict]:
    payload = json.dumps({"task": "continue", "stream": True}).encode()
    req = urllib.request.Request(base + "/api/ai", data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    frames = []
    current = {}
    with urllib.request.urlopen(req, timeout=8) as response:
        assert response.status == 200
        while True:
            raw = response.readline()
            if not raw:
                break
            line = raw.decode().rstrip("\r\n")
            if line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("id: "):
                current["id"] = int(line[4:])
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
            elif line == "" and current:
                if current.get("event") == "delta" and not any(item["event"] == "delta" for item in frames):
                    assert not ProviderHandler.finished.is_set(), "first chunk arrived only after provider completed"
                    assert not ProviderHandler.observation_timed_out.is_set(), "first persisted chunk was buffered"
                    ProviderHandler.first_chunk_observed.set()
                frames.append(current)
                current = {}
    return frames


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
            request(base, "PUT", "/api/ai/settings", {"include_usage": True})
            frames = live_generation(base)
            deltas = [item for item in frames if item["event"] == "delta"]
            done = next(item["data"] for item in frames if item["event"] == "done")
            assert [item["id"] for item in deltas] == [1, 2]
            assert "".join(item["data"]["text"] for item in deltas) == "第一段输出"
            assert done["stream_transport"] == "provider_native_sse"
            assert done["status"] == "success" and done["completion_state"] == "complete"
            assert done["safe_to_apply"] and done["finish_reason"] == "stop"
            assert done["text"] == "第一段输出" and done["usage"]["total_tokens"] == 49
            assert done["requested_model"] == done["returned_model"] == "fixture-model"
            assert ProviderHandler.payload["stream"] and ProviderHandler.payload["stream_options"]["include_usage"]
            run_id = done["ai_run_id"]
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
