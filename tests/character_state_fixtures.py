#!/usr/bin/env python3
"""Regression fixture for character-state and structured plot-thread APIs."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "MOYU_DB": tempfile.mktemp(suffix=".sqlite3"), "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(port)}
    process = subprocess.Popen([os.sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"

    def call(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = urllib.request.Request(base + path, method=method, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    try:
        for _ in range(50):
            try:
                status, _ = call("/api/health")
                if status == 200:
                    break
            except Exception:
                time.sleep(0.05)
        _, state = call("/api/state")
        entity_id = state["records"][0]["id"]

        status, created = call("/api/character-states", "POST", {
            "id": "cstate_fixture", "entity_id": entity_id, "attribute": "当前情绪",
            "value_json": {"value": "紧张", "confidence": 0.8},
            "knowledge_scope_json": {"characters": [entity_id]}, "valid_start": "chapter-1",
        })
        assert status == 201 and created["value"]["value"] == "紧张", created
        status, listed = call("/api/story/character-states?entity_id=" + entity_id)
        assert status == 200 and listed["count"] == 1 and listed["character_states"][0]["id"] == "cstate_fixture"
        status, changed = call("/api/character-states/cstate_fixture", "PATCH", {"value_json": {"value": "警觉"}})
        assert status == 200 and changed["value"]["value"] == "警觉", changed
        status, invalid = call("/api/character-states", "POST", {"entity_id": "other-project-entity", "attribute": "x", "value_json": {}})
        assert status == 400 and "entity" in invalid.get("error", "")

        status, thread = call("/api/plot-threads", "POST", {"id": "thread_fixture", "status": "open", "severity": "high", "payload_json": {"question": "谁在敲门？"}})
        assert status == 201 and thread["payload"]["question"] == "谁在敲门？", thread
        status, structure = call("/api/story-map")
        assert any(item["id"] == "cstate_fixture" for item in structure["character_states"])
        assert any(item["id"] == "thread_fixture" for item in structure["plot_threads"])
        status, _ = call("/api/plot-threads/thread_fixture", "PATCH", {"status": "unknown"})
        assert status == 400
        status, deleted = call("/api/story/character-states/cstate_fixture", "DELETE", {"reason": "fixture cleanup"})
        assert status == 200 and deleted["deleted"] == "cstate_fixture"
        status, deleted = call("/api/plot-threads/thread_fixture", "DELETE", {"reason": "fixture cleanup"})
        assert status == 200 and deleted["deleted"] == "thread_fixture"
    finally:
        process.terminate()
        process.wait(timeout=3)
    print("character state fixtures: ok")


if __name__ == "__main__":
    main()
