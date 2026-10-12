#!/usr/bin/env python3
"""Exercise the prewrite/precommit/postcommit chapter commit lifecycle."""
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
    env = {**os.environ, "MOYU_DB": tempfile.mktemp(suffix=".sqlite3"), "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(port), "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": ""}
    process = subprocess.Popen([os.sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if request(base, "GET", "/api/health")[0] == 200:
                    break
            except Exception:
                time.sleep(0.05)
        state = request(base, "GET", "/api/state")[1]
        chapter = state["chapters"][0]
        blocked_status, blocked = request(base, "POST", f"/api/chapters/{chapter['id']}/commit", {"stage": "prewrite"})
        assert blocked_status == 409 and blocked["status"] == "blocked"
        precommit_status, precommit = request(base, "POST", f"/api/chapters/{chapter['id']}/commit", {"stage": "precommit"})
        assert precommit_status == 409 and precommit["status"] == "blocked"
        patch = {"body": "沈砚推开旧门，雨水沿着袖口滴落。\n\n门后的脚步停了一瞬。" * 4, "status": "完成", "chapter_goal": "确认门后的声音", "scene_plan": "入口、阻力、转折、出口", "pov": "沈砚"}
        assert request(base, "PATCH", f"/api/chapters/{chapter['id']}", patch)[0] == 200
        prewrite_status, prewrite = request(base, "POST", f"/api/chapters/{chapter['id']}/commit", {"stage": "prewrite"})
        assert prewrite_status == 201 and prewrite["status"] == "passed"
        precommit_status, precommit = request(base, "POST", f"/api/chapters/{chapter['id']}/commit", {"stage": "precommit"})
        assert precommit_status == 201 and precommit["status"] == "passed"
        postcommit_status, postcommit = request(base, "POST", f"/api/chapters/{chapter['id']}/commit", {"stage": "postcommit"})
        assert postcommit_status == 201 and postcommit["status"] == "passed"
        history_status, history = request(base, "GET", f"/api/chapters/{chapter['id']}/commits")
        assert history_status == 200 and len(history["commits"]) == 5
    finally:
        process.terminate()
        process.wait(timeout=3)
    print("chapter commit fixtures: ok")


if __name__ == "__main__":
    main()
