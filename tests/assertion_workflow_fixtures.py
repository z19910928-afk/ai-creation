#!/usr/bin/env python3
"""Regression fixture for assertion detail, evidence invalidation and review gates."""
from __future__ import annotations
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def main() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env = {**os.environ, "MOYU_DB": tempfile.mktemp(suffix=".sqlite3"), "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    def call(path: str, method: str = "GET", payload: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = urllib.request.Request(base + path, method=method, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())
    try:
        for _ in range(50):
            try:
                if call("/api/health")[0] == 200: break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(.05)
        state = call("/api/state")[1]; chapter = state["chapters"][0]
        quote = "沈砚握紧旧钥匙。"
        body = f"{quote}\n雨声压住钟声。\n{quote}\n" + "他没有回头。" * 12
        assert call(f"/api/chapters/{chapter['id']}", "PATCH", {"body": body, "status": "完成"})[0] == 200
        assert call("/api/records", "POST", {"type": "人物", "name": "沈砚", "description": "主角"})[0] == 201
        status, memory = call("/api/memory", "POST", {"status": "accepted", "type": "人物", "name": "沈砚", "source_chapter_id": chapter["id"], "source_quote": quote, "source_start": 0, "source_end": len(quote)})
        assert status == 201 and memory.get("assertion_id"), memory
        status, tampered = call(f"/api/memory/{memory['id']}", "PATCH", {"status": "accepted", "source_quote": "伪造事实"})
        assert status == 409 and "evidence" in tampered["error"], tampered
        status, renamed = call(f"/api/memory/{memory['id']}", "PATCH", {"status": "accepted", "name": "陈默"})
        assert status == 409 and "identity" in renamed["error"], renamed
        aid = memory["assertion_id"]
        status, detail = call(f"/api/assertions/{aid}")
        assert status == 200 and detail["evidence"] and detail["evidence"][0]["source_span"]["quote"] == quote, detail
        status, listing = call("/api/assertions?state=canon")
        assert status == 200 and any(item["id"] == aid for item in listing["assertions"])
        status, evidence = call(f"/api/memory/{memory['id']}/evidence")
        assert status == 200 and evidence["evidence"], evidence
        # Any chapter revision invalidates the old source span. The assertion
        # remains inspectable but cannot be canonized again until re-anchored.
        assert call(f"/api/chapters/{chapter['id']}", "PATCH", {"body": body + "\n新增一段。"})[0] == 200
        status, stale = call(f"/api/assertions/{aid}")
        assert status == 200 and stale["stale"] and stale["stale_reasons"], stale
        status, stale_list = call("/api/assertions?stale_only=1")
        assert status == 200 and any(item["id"] == aid for item in stale_list["assertions"])
        status, ctx = call(f"/api/context?chapter_id={chapter['id']}")
        assert status == 200 and all(item["id"] != memory["id"] for item in ctx["memory"]), ctx["memory"]
        status, blocked = call(f"/api/assertions/{aid}/review", "POST", {"state": "canon", "reason": "尝试重新确认"})
        assert status == 409 and "stale" in blocked["error"], blocked
        status, reviewed = call(f"/api/assertions/{aid}/review", "POST", {"state": "candidate", "reason": "原章节已改写，等待重新定位"})
        assert status == 200 and reviewed["state"] == "candidate", reviewed
        status, reanchored = call(f"/api/memory/{memory['id']}/reanchor", "POST", {"source_chapter_id": chapter["id"], "source_quote": quote, "source_start": 0, "source_end": len(quote), "status": "accepted"})
        assert status == 200 and reanchored.get("source_span_status") == "valid", reanchored
        status, ctx_after = call(f"/api/context?chapter_id={chapter['id']}")
        assert status == 200 and any(item["id"] == memory["id"] for item in ctx_after["memory"]), ctx_after["memory"]
        status, assertion_cards = call(f"/api/assertions?memory_id={memory['id']}")
        canon_cards = [item for item in assertion_cards["assertions"] if item["state"] == "canon"]
        assert status == 200 and len(canon_cards) == 1, assertion_cards
        status, demoted = call(f"/api/memory/{memory['id']}", "PATCH", {"status": "candidate", "reason": "需要重新审查"})
        assert status == 200, demoted
        status, assertion_cards = call(f"/api/assertions?memory_id={memory['id']}")
        assert status == 200 and all(item["state"] != "canon" for item in assertion_cards["assertions"]), assertion_cards
        status, contradicted = call(f"/api/assertions/{aid}/review", "POST", {"state": "contradicted", "reason": "测试终态 assertion"})
        assert status == 200 and contradicted["state"] == "contradicted", contradicted
        status, reanchored_again = call(f"/api/memory/{memory['id']}/reanchor", "POST", {"source_chapter_id": chapter["id"], "source_quote": quote, "source_start": 0, "source_end": len(quote), "status": "accepted"})
        assert status == 200, reanchored_again
        status, assertion_cards = call(f"/api/assertions?memory_id={memory['id']}")
        assert status == 200 and sum(item["state"] == "canon" for item in assertion_cards["assertions"]) == 1 and sum(item["state"] == "contradicted" for item in assertion_cards["assertions"]) == 1, assertion_cards
        events = call("/api/events?limit=200")[1]["events"]
        assert any(item["event_type"] == "assertion.reviewed" and item["payload_json"].get("reason") for item in events)
    finally:
        proc.terminate(); proc.wait(timeout=3)
    print("assertion workflow fixtures: ok")
if __name__ == "__main__": main()
