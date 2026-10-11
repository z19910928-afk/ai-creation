#!/usr/bin/env python3
"""Exercise the runtime continuity path against an isolated SQLite service."""

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
sys.path.insert(0, str(ROOT))
from server import fit_context_budget  # noqa: E402


def call(port: int, method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    oversized_context = {
        "project": {"title": "x", "summary": "y" * 20_000},
        "current_chapter": {"body_tail": "z" * 60_000},
        "story_cards": [{"description": "a" * 2_000} for _ in range(40)],
        "confirmed_memory": [{"note": "m" * 2_000} for _ in range(60)],
        "plot": {stage: [{"text": "p" * 2_000} for _ in range(30)] for stage in ("setup", "develop", "payoff")},
        "retrieved_chapters": [{"excerpt": "r" * 2_600, "recap": "q" * 2_000} for _ in range(20)],
        "previous_chapters": [{"excerpt": "h" * 3_000} for _ in range(3)],
    }
    assert fit_context_budget(oversized_context, 24_000) <= 24_000
    port = unused_port()
    with tempfile.TemporaryDirectory(prefix="moyu-runtime-") as tmp:
        database = Path(tmp) / "runtime.sqlite3"
        environment = {
            **os.environ,
            "MOYU_DB": str(database),
            "MOYU_HOST": "127.0.0.1",
            "MOYU_PORT": str(port),
            "MOYU_AI_BASE_URL": "",
            "MOYU_AI_MODEL": "",
            "MOYU_AI_API_KEY": "",
        }
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(50):
                try:
                    health = call(port, "GET", "/api/health")
                    if health.get("schema_version") == 11:
                        break
                except (urllib.error.URLError, ConnectionError):
                    time.sleep(0.05)
            else:
                raise AssertionError("SQLite service did not become ready")

            body = (
                "【人物】林岚。林岚在暴雨前打开了旧门，听见门后传来三次敲击。"
                "她没有退后，而是把钥匙交给守在台阶下的陌生人。这个决定改变了回城路线。"
            )
            state = {
                "full_state": True,
                "project": {"title": "运行时连续性样本", "genre": "悬疑", "summary": "一个选择打开了旧门。", "goal": 50000, "book_status": "writing"},
                "chapters": [
                    {
                        "id": "ch_runtime",
                        "title": "第一章 · 旧门",
                        "body": body,
                        "status": "完成",
                        "chapter_goal": "让林岚在阻力中做出不可撤回的选择。",
                        "scene_plan": "入口：暴雨前的旧门。转折：钥匙交给陌生人。出口：路线改变。",
                        "pov": "林岚",
                        "foreshadowing_op": "门后敲击声",
                        "hook": "门后第三次敲击来自谁？",
                        "recap": "林岚打开旧门并把钥匙交给陌生人。",
                        "next_brief": "下一章从陌生人带走钥匙后的路线开始。",
                        "review_status": "unreviewed",
                    }
                ],
                "records": [{"id": "ent_lan", "type": "人物", "name": "林岚", "description": "在压力下会主动承担代价。"}],
                "plot": {"setup": ["门后敲击声"], "develop": [], "payoff": []},
                "memory": [
                    {
                        "id": "mem_runtime",
                        "key": "人物:林岚",
                        "type": "人物",
                        "name": "林岚",
                        "source": "第一章 · 旧门",
                        "source_chapter_id": "ch_runtime",
                        "source_quote": "【人物】林岚",
                        "note": "主角。",
                        "status": "已确认",
                    }
                ],
            }
            synced = call(port, "PUT", "/api/state", state)
            assert synced["chapters"][0]["id"] == "ch_runtime"
            memory = next(item for item in synced["memory"] if item["id"] == "mem_runtime")
            assert memory["source_span_id"]
            assert memory["source_start"] == 0

            review = call(port, "POST", "/api/review", {"chapter_id": "ch_runtime"})
            assert review["status"] == "passed", review
            context = call(port, "GET", "/api/context?chapter_id=ch_runtime")
            assert context["budget"]["estimated_tokens"] <= context["budget"]["max_tokens"], context["budget"]
            assert context["budget"]["estimated_chars"] <= context["budget"]["max_tokens"] * 4, context["budget"]
            assert "ch_runtime" not in context["budget"]["context_item_ids"]
            ai = call(port, "POST", "/api/ai", {"task": "summary", "chapter_id": "ch_runtime", "instruction": "只给核对提示"})
            assert ai["mode"] == "offline" and ai["ai_run_id"].startswith("airun_")

            with sqlite3.connect(database) as connection:
                span = connection.execute("SELECT start_codepoint,end_codepoint,status FROM source_span WHERE document_node_id='ch_runtime'").fetchone()
                assertion = connection.execute("SELECT state,evidence_json,reviewed_by FROM assertion WHERE subject_entity_id='ent_lan'").fetchone()
                run = connection.execute("SELECT status,author_decision,latency_ms FROM ai_run WHERE id=?", (ai["ai_run_id"],)).fetchone()
            assert span and span[0] == 0 and span[1] > 0 and span[2] == "valid"
            assert assertion and assertion[0] == "canon" and "mem_runtime" in assertion[1] and assertion[2]
            assert run and run[0] == "success" and run[1] == "pending" and run[2] is not None
            print("runtime continuity fixtures: ok")
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


if __name__ == "__main__":
    main()
