#!/usr/bin/env python3
"""Small regression fixture for the story structure API."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    env = {**os.environ, "MOYU_DB": tempfile.mktemp(suffix=".sqlite3"), "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(port)}
    process = subprocess.Popen([os.sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1); break
            except Exception: time.sleep(.05)
        def call(path: str, method: str = "GET", payload: dict | None = None, statuses=(200, 201)) -> dict:
            req = urllib.request.Request(base + path, method=method, data=json.dumps(payload).encode() if payload else None, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as response:
                assert response.status in statuses
                return json.load(response)
        state = call("/api/state"); first, second = state["records"][:2]
        relation = call("/api/relationships", "POST", {"from_entity_id": first["id"], "to_entity_id": second["id"], "relation_type": "盟友"})
        event = call("/api/timeline", "POST", {"title": "首次相遇", "entity_ids": [first["id"]], "ordinal": 1})
        plot = call("/api/plot", "POST", {"text": "反复出现的钟声"})
        link = call("/api/foreshadow-links", "POST", {"plot_id": plot["id"], "chapter_id": state["chapters"][0]["id"], "kind": "plant"})
        call(f"/api/relationships/{relation['id']}", "PATCH", {"relation_type": "敌对", "note": "利益冲突"})
        call(f"/api/timeline/{event['id']}", "PATCH", {"title": "首次相遇（修订）", "status": "canon"})
        call(f"/api/foreshadow-links/{link['id']}", "PATCH", {"kind": "develop", "note": "逐步加强"})
        structure = call("/api/story-map")
        assert structure["relationships"][0]["relation_type"] == "敌对"
        assert structure["timeline"][0]["title"] == "首次相遇（修订）"
        assert structure["foreshadowing"][0]["kind"] == "develop"
        call(f"/api/relationships/{relation['id']}", "DELETE", {"reason": "fixture cleanup"})
        call(f"/api/timeline/{event['id']}", "DELETE", {"reason": "fixture cleanup"})
        call(f"/api/foreshadow-links/{link['id']}", "DELETE", {"reason": "fixture cleanup"})
        empty = call("/api/story-map")
        assert not empty["relationships"] and not empty["timeline"] and not empty["foreshadowing"]
    finally:
        process.terminate(); process.wait(timeout=3)
    print("story structure fixtures: ok")

if __name__ == "__main__":
    main()
