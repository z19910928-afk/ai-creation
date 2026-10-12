#!/usr/bin/env python3
"""Exercise the read-only projection doctor and its consistency signals."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def request(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    raw = json.dumps(payload or {}, ensure_ascii=False).encode()
    req = urllib.request.Request(base + path, method=method, data=raw, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def main() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "MOYU_DB": str(Path(tempfile.mkdtemp(prefix="moyu-doctor-") ) / "doctor.sqlite3"),
        "MOYU_HOST": "127.0.0.1",
        "MOYU_PORT": str(port),
        "MOYU_AI_BASE_URL": "",
        "MOYU_AI_MODEL": "",
    }
    process = subprocess.Popen([os.sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if request(base, "GET", "/api/health")[0] == 200:
                    break
            except Exception:
                time.sleep(0.05)

        status, initial = request(base, "GET", "/api/doctor")
        assert status == 200
        assert initial["branch"]["id"] == "main"
        assert initial["state"]["version"] == initial["state"]["event_count"]
        assert initial["search_index"]["status"] == "missing"
        assert any(item["code"] == "search_index_missing" for item in initial["issues"])

        # A normal search refreshes the local projection; doctor observes it
        # without refreshing or mutating anything itself.
        assert request(base, "GET", "/api/search?" + urllib.parse.urlencode({"q": "故事"}))[0] == 200
        current_status, current = request(base, "GET", "/api/projection-health")
        assert current_status == 200 and current["search_index"]["status"] == "current"
        assert current["ok"] is True

        queue_status, queued = request(base, "POST", "/api/ai/queue", {"task": "scene", "response_format": "json", "defer": True})
        assert queue_status == 202 and queued["status"] == "queued"
        pending = request(base, "GET", "/api/health/projections")[1]
        assert pending["ai_queue"]["pending"] >= 1
        assert pending["ai_queue"]["counts"]["queued"] >= 1

        chapter = request(base, "GET", "/api/state")[1]["chapters"][0]
        patch = {
            "body": "沈砚推开旧门，雨水沿着袖口滴落。\n\n门后的脚步停了一瞬。" * 3,
            "status": "完成",
            "chapter_goal": "确认门后的声音",
            "scene_plan": "入口、阻力、转折、出口",
            "pov": "沈砚",
        }
        assert request(base, "PATCH", f"/api/chapters/{chapter['id']}", patch)[0] == 200
        assert request(base, "POST", f"/api/chapters/{chapter['id']}/commit", {"stage": "prewrite"})[0] == 201
        assert request(base, "POST", f"/api/chapters/{chapter['id']}/commit", {"stage": "precommit"})[0] == 201
        assert request(base, "POST", f"/api/chapters/{chapter['id']}/commit", {"stage": "postcommit"})[0] == 201
        after_commit = request(base, "GET", "/api/doctor")[1]
        latest = after_commit["commit"]["latest"]
        assert latest["stage"] == "postcommit" and latest["status"] in {"passed", "overridden"}, latest
        assert any(item["chapter_id"] == chapter["id"] and item["stage"] == "postcommit" for item in after_commit["commit"]["chapters"])
        assert after_commit["ai_queue"]["pending"] >= 1
    finally:
        process.terminate()
        process.wait(timeout=3)
    print("projection health fixtures: ok")


if __name__ == "__main__":
    main()
