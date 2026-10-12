#!/usr/bin/env python3
"""Regression checks for branch-head optimistic concurrency on manuscript writes."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def request(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, method=method, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    database = tempfile.mktemp(prefix="moyu-cas-", suffix=".sqlite3")
    environment = {**os.environ, "MOYU_DB": database, "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(port)}
    process = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if request(base, "GET", "/api/health")[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.05)
        status, state = request(base, "GET", "/api/state")
        assert status == 200 and state.get("head_revision")
        initial_head = state["head_revision"]
        chapter = state["chapters"][0]

        status, conflict = request(base, "POST", "/api/chapters", {"title": "冲突创建", "body": "x", "expected_head": "stale-head"})
        assert status == 409 and conflict.get("current_head") == initial_head, conflict
        status, created = request(base, "POST", "/api/chapters", {"title": "并发章节", "body": "第一版", "expected_head": initial_head})
        assert status == 201, created
        status, after_create = request(base, "GET", "/api/state")
        assert status == 200 and after_create["head_revision"] != initial_head

        status, conflict = request(base, "PATCH", f"/api/chapters/{created['id']}", {"body": "过期写入", "expected_head": initial_head})
        assert status == 409 and "expected_head" in conflict.get("error", ""), conflict
        current_head = after_create["head_revision"]
        status, changed = request(base, "PATCH", f"/api/chapters/{created['id']}", {"body": "第二版", "expected_head": current_head})
        assert status == 200 and changed["body"] == "第二版", changed

        status, revisions = request(base, "GET", f"/api/chapters/{created['id']}/revisions")
        assert status == 200 and len(revisions["revisions"]) >= 2
        status, state = request(base, "GET", "/api/state")
        current_head = state["head_revision"]
        target_revision = revisions["revisions"][-1]["id"]
        status, conflict = request(base, "POST", f"/api/chapters/{created['id']}/restore-revision", {"revision_id": target_revision, "expected_head": initial_head})
        assert status == 409, conflict
        status, restored = request(base, "POST", f"/api/chapters/{created['id']}/restore-revision", {"revision_id": target_revision, "expected_head": current_head})
        assert status == 200, restored

        status, state = request(base, "GET", "/api/state")
        current_head = state["head_revision"]
        status, created_again = request(base, "POST", "/api/chapters", {"title": "待删章节", "body": "删除测试", "expected_head": current_head})
        assert status == 201, created_again
        status, state = request(base, "GET", "/api/state")
        newest_head = state["head_revision"]
        status, conflict = request(base, "DELETE", f"/api/chapters/{created_again['id']}", {"expected_head": current_head})
        assert status == 409, conflict
        status, deleted = request(base, "DELETE", f"/api/chapters/{created_again['id']}", {"expected_head": newest_head})
        assert status == 200 and deleted["deleted"] == created_again["id"], deleted

        # PUT /api/state supports a branch head token independently of its
        # numeric projection version used by the existing browser sync path.
        status, state = request(base, "GET", "/api/state")
        stale_state = dict(state)
        stale_state["expected_head"] = initial_head
        status, conflict = request(base, "PUT", "/api/state", stale_state)
        assert status == 409 and conflict.get("current_head") == state["head_revision"], conflict
    finally:
        process.terminate()
        process.wait(timeout=3)
    print("branch CAS fixtures: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
