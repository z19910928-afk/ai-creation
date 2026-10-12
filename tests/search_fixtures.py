#!/usr/bin/env python3
"""Regression fixture for the lexical search API and source metadata."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.parse
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

    def call(path: str, method: str = "GET", payload: dict | None = None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = urllib.request.Request(base + path, method=method, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    try:
        for _ in range(50):
            try:
                call("/api/health")
                break
            except Exception:
                time.sleep(0.05)
        state = call("/api/state")
        chapter = call("/api/chapters", "POST", {"title": "雾港回声", "body": "钟声穿过雾港，沈砚握紧旧钥匙。", "status": "进行中"})
        record = call("/api/records", "POST", {"type": "物品", "name": "旧钥匙", "description": "能打开北塔的铜钥匙。"})
        call("/api/memory", "POST", {"type": "人物", "name": "沈砚", "source_chapter_id": chapter["id"], "source_quote": "沈砚握紧旧钥匙。"})
        plot = call("/api/plot", "POST", {"text": "雾港的钟声指向北塔"})
        call("/api/timeline", "POST", {"title": "北塔钟响", "summary": "雾港第一次听见钟声", "event_time": "0003-02-01", "chapter_id": chapter["id"]})
        call("/api/foreshadow-links", "POST", {"plot_id": plot["id"], "chapter_id": chapter["id"], "kind": "plant"})

        def search(term: str, **filters: str) -> list[dict]:
            path = "/api/search?" + urllib.parse.urlencode({"q": term, "limit": 20, **filters})
            return call(path)["results"]

        assert any(item["type"] == "chapter" and item["source_chapter_id"] == chapter["id"] for item in search("雾港回声"))
        assert any(item["type"] == "setting" for item in search("北塔"))
        memory = search("沈砚")
        assert any(item["type"] == "memory" and item["status"] == "待确认" for item in memory)
        assert any(item["type"] == "foreshadow" for item in search("钟声"))
        assert any(item["type"] == "timeline" and item["source_chapter_id"] == chapter["id"] for item in search("北塔钟响"))
        # The local FTS projection indexes overlapping CJK grams, so a partial
        # phrase that is not a complete title still returns its source.
        assert any(item["type"] == "chapter" for item in search("声穿过"))
        assert all(item["type"] == "setting" for item in search("北塔", entity_id=record["id"]))
        assert any(item["type"] == "timeline" for item in search("钟声", time_from="0003-01-01", time_to="0003-12-31", kind="timeline"))
        assert not search("钟声", time_from="0004-01-01", kind="timeline")
    finally:
        process.terminate()
        process.wait(timeout=3)
    print("search fixtures: ok")


if __name__ == "__main__":
    main()
