#!/usr/bin/env python3
"""Exercise durable AI queue, cancellation, processing and retry semantics."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def call(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(base + path, data=body, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_status(base: str, job_id: str, expected: set[str]) -> dict:
    for _ in range(50):
        _, result = call(base, "GET", "/api/ai/jobs/" + job_id)
        if result["status"] in expected:
            return result
        time.sleep(0.05)
    raise AssertionError(f"job did not reach {expected}: {result}")


def main() -> None:
    port = unused_port()
    with tempfile.TemporaryDirectory(prefix="moyu-ai-queue-") as tmp:
        database = str(Path(tmp) / "queue.sqlite3")
        environment = {
            **os.environ,
            "MOYU_DB": database,
            "MOYU_HOST": "127.0.0.1",
            "MOYU_PORT": str(port),
            "MOYU_AI_BASE_URL": "",
            "MOYU_AI_MODEL": "",
        }
        process = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(50):
                try:
                    call(base, "GET", "/api/health")
                    break
                except Exception:
                    time.sleep(0.05)
            status, queued = call(base, "POST", "/api/ai/queue", {"task": "scene", "response_format": "json", "defer": True})
            assert status == 202 and queued["status"] == "queued" and queued["auto_started"] is False
            job_id = queued["id"]
            cancel_status, canceled = call(base, "POST", f"/api/ai/jobs/{job_id}/cancel", {"reason": "作者撤回"})
            assert cancel_status == 200 and canceled["status"] == "canceled"
            canceled_detail = wait_status(base, job_id, {"canceled"})
            assert canceled_detail["cancel_requested"] is True

            status, deferred = call(base, "POST", "/api/ai/queue", {"task": "scene", "response_format": "json", "defer": True})
            assert status == 202
            process_status, completed = call(base, "POST", f"/api/ai/jobs/{deferred['id']}/process", {})
            assert process_status == 200 and completed["status"] == "success"
            assert completed.get("result", {}).get("structured", {}).get("pov")

            retry_status, retry = call(base, "POST", f"/api/ai/jobs/{job_id}/retry", {})
            assert retry_status == 202 and retry["retry_of"] == queued["ai_run_id"]
            retry_detail = wait_status(base, retry["id"], {"success"})
            assert retry_detail["attempt"] == 1
            queue_status, queue = call(base, "GET", "/api/ai/queue")
            assert queue_status == 200 and queue["counts"].get("success", 0) >= 2
        finally:
            process.terminate()
            process.wait(timeout=3)
    print("AI queue fixtures: ok")


if __name__ == "__main__":
    main()
