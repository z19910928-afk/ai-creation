#!/usr/bin/env python3
"""Regression checks for the strict author/evidence/AI audit boundary."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, data=body, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def start(env: dict[str, str]) -> tuple[subprocess.Popen[str], str, Path]:
    database = Path(tempfile.mktemp(prefix="moyu-strict-", suffix=".sqlite3"))
    selected_port = port()
    process_env = {**os.environ, **env, "MOYU_DB": str(database), "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(selected_port)}
    process = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=process_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{selected_port}"
    for _ in range(50):
        try:
            if request(base, "GET", "/api/health")[0] == 200:
                return process, base, database
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.05)
    process.kill()
    raise AssertionError("service did not become ready")


def stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def main() -> None:
    process, base, database = start({"MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": ""})
    try:
        state = request(base, "GET", "/api/state")[1]
        chapter = state["chapters"][0]
        body = "【人物】沈砚在雨里推开旧门。" * 8
        assert request(base, "PATCH", f"/api/chapters/{chapter['id']}", {"body": body, "status": "完成"})[0] == 200
        bad_status, bad = request(base, "POST", "/api/memory", {"status": "accepted", "type": "人物", "name": "沈砚"})
        assert bad_status == 400 and "source quote" in bad["error"]
        assert request(base, "POST", "/api/records", {"type": "人物", "name": "沈砚", "description": "主角"})[0] == 201
        good_status, good = request(base, "POST", "/api/memory", {"status": "accepted", "type": "人物", "name": "沈砚", "source_chapter_id": chapter["id"], "source_quote": "【人物】沈砚"})
        assert good_status == 201 and good.get("assertion_id")
        ai_status, ai = request(base, "POST", "/api/ai", {"task": "continue", "chapter_id": chapter["id"]})
        assert ai_status == 200 and ai.get("ai_run_id")
        decision_status, decision = request(base, "POST", "/api/ai/decision", {"ai_run_id": ai["ai_run_id"], "decision": "accepted", "reason": "作者核对通过"})
        assert decision_status == 201 and decision["author_decision"] == "accepted"
        audit_status, audit = request(base, "GET", f"/api/ai/runs/{ai['ai_run_id']}")
        assert audit_status == 200 and audit["decisions"][0]["decision"] == "accepted"
        assert request(base, "PATCH", f"/api/chapters/{chapter['id']}", {"body": body + "\nsecond revision"})[0] == 200
        history_status, history = request(base, "GET", f"/api/chapters/{chapter['id']}/revisions")
        assert history_status == 200 and len(history["revisions"]) >= 2
        restored_status, restored = request(base, "POST", f"/api/chapters/{chapter['id']}/restore-revision", {"revision_id": history["revisions"][-1]["id"]})
        assert restored_status == 200 and "把故事写在这里" in restored["body"]
    finally:
        stop(process)

    process, base, database = start({"MOYU_AI_BASE_URL": "http://127.0.0.1:9/v1", "MOYU_AI_MODEL": "fixture-model"})
    try:
        chapter = request(base, "GET", "/api/state")[1]["chapters"][0]
        failed_status, failed = request(base, "POST", "/api/ai", {"task": "continue", "chapter_id": chapter["id"]})
        assert failed_status == 502 and failed.get("ai_run_id")
        with sqlite3.connect(database) as connection:
            row = connection.execute("SELECT status,error,output_hash FROM ai_run WHERE id=?", (failed["ai_run_id"],)).fetchone()
        assert row and row[0] == "failed" and row[1] and row[2] is None
    finally:
        stop(process)
    print("strict feature fixtures: ok")


if __name__ == "__main__":
    main()
