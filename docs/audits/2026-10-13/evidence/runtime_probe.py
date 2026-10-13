#!/usr/bin/env python3
"""Read-only audit against isolated databases and a free loopback provider."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ARTIFACT = Path(__file__).with_name("runtime-results.json")
RESULTS = {}


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base, method, path, payload=None, timeout=10):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(base + path, method=method, data=data, headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read().decode()
        return response.status, json.loads(raw) if response.headers.get_content_type() == "application/json" else raw


@contextmanager
def app(extra=None):
    app_port = port()
    with tempfile.TemporaryDirectory(prefix="moyu-audit-c-") as temporary:
        database = str(Path(temporary) / "probe.sqlite3")
        env = {
            **os.environ,
            "MOYU_DB": database,
            "MOYU_HOST": "127.0.0.1",
            "MOYU_PORT": str(app_port),
            "MOYU_AI_BASE_URL": "",
            "MOYU_AI_MODEL": "",
            "MOYU_AI_API_KEY": "",
            "MOYU_AI_DAILY_QUOTA": "0",
            **(extra or {}),
        }
        process = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{app_port}"
        try:
            for _ in range(100):
                try:
                    if request(base, "GET", "/api/health")[0] == 200:
                        break
                except Exception:
                    time.sleep(0.02)
            else:
                raise AssertionError("server failed to start")
            yield base, process, database
        finally:
            process.terminate()
            process.wait(timeout=5)


class Provider(BaseHTTPRequestHandler):
    request_count = 0
    stream_delay = 0
    json_delay = 0
    sent_first = threading.Event()

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Provider.request_count += 1
        self.send_response(200)
        if payload.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write('data: {"choices":[{"delta":{"content":"第一"},"finish_reason":null}]}\n\n'.encode())
            self.wfile.flush()
            Provider.sent_first.set()
            time.sleep(Provider.stream_delay)
            self.wfile.write('data: {"choices":[{"delta":{"content":"段输出"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'.encode())
            self.wfile.flush()
        else:
            time.sleep(Provider.json_delay)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"model": "upstream-model", "usage": {"prompt_tokens": 200, "completion_tokens": 10}, "choices": [{"message": {"content": "模拟候选正文"}, "finish_reason": "stop"}]}, ensure_ascii=False).encode())

    def log_message(self, *_):
        pass


def record(name, callback):
    try:
        RESULTS[name] = callback()
    except Exception as exc:
        RESULTS[name] = {"probe_error": type(exc).__name__, "message": str(exc)}
    ARTIFACT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2))
    print(name + ": " + json.dumps(RESULTS[name], ensure_ascii=False), flush=True)


def memory_anchor():
    with app() as (base, _, _):
        _, state = request(base, "GET", "/api/state")
        chapter = state["chapters"][0]
        quote = "【人物】新人物"
        chapter["body"] = "开头。\n" + quote + "\n其余正文。"
        state["memory"] = [{"id": "audit_memory", "type": "人物", "name": "新人物", "key": "人物:新人物", "source": chapter["title"], "source_chapter_id": chapter["id"], "source_quote": quote, "source_start": 4, "source_end": 4 + len(quote), "status": "已确认", "confidence": "已确认"}]
        first_status, state = request(base, "PUT", "/api/state", state)
        assert first_status == 200, state
        original_body = state["chapters"][0]["body"]
        state["chapters"][0]["body"] = "新增一句。\n" + original_body
        status, failed = request(base, "PUT", "/api/state", state)
        _, persisted = request(base, "GET", "/api/state")
        return {"confirmed_save_http": first_status, "edit_http": status, "error": failed, "new_text_persisted": persisted["chapters"][0]["body"] != original_body}


def deleted_restore():
    with app() as (base, _, _):
        _, backup = request(base, "GET", "/api/state")
        chapter_id = backup["chapters"][0]["id"]
        deleted, _ = request(base, "DELETE", "/api/chapters/" + chapter_id)
        backup.pop("version", None)
        status, result = request(base, "PUT", "/api/state", backup)
        _, current = request(base, "GET", "/api/state")
        return {"delete_http": deleted, "restore_import_http": status, "error": result, "chapters_after_import": len(current["chapters"])}


def decision_conflict():
    with app() as (base, _, _):
        _, state = request(base, "GET", "/api/state")
        version = state["version"]
        status, ai = request(base, "POST", "/api/ai", {"task": "continue", "chapter_id": state["chapters"][0]["id"]})
        decided, _ = request(base, "POST", "/api/ai/decision", {"ai_run_id": ai["ai_run_id"], "decision": "accepted"})
        state["chapters"][0]["body"] += "\n采纳候选"
        saved, error = request(base, "PUT", "/api/state", state)
        _, after = request(base, "GET", "/api/state")
        return {"ai_http": status, "decision_http": decided, "original_version": version, "server_version": after["version"], "adoption_save_http": saved, "error": error, "candidate_in_database": "采纳候选" in after["chapters"][0]["body"]}


def rejection_contract():
    with app() as (base, _, _):
        status, memory = request(base, "POST", "/api/memory", {"id": "reject_probe", "type": "人物", "name": "新人物"})
        rejected, result = request(base, "PATCH", "/api/memory/reject_probe", {"status": "rejected"})
        _, state = request(base, "GET", "/api/state")
        return {"create_http": status, "reject_http": rejected, "state_memory_status": state["memory"][0]["status"], "ui_accepted_statuses": ["待确认", "已确认", "candidate", "accepted", "rejected", "stale", "superseded"], "contract_mismatch": state["memory"][0]["status"] == "已拒绝"}


def streaming(provider):
    Provider.stream_delay = 1.25
    Provider.sent_first.clear()
    env = {"MOYU_AI_BASE_URL": f"http://127.0.0.1:{provider.server_address[1]}", "MOYU_AI_MODEL": "audit-model"}
    with app(env) as (base, _, database):
        request_obj = urllib.request.Request(base + "/api/ai", method="POST", data=b'{"task":"continue","stream":true}', headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            header_elapsed = time.perf_counter() - started
            body = response.read().decode()
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        run_id = events[0]["ai_run_id"]
        _, replay = request(base, "GET", f"/api/ai/runs/{run_id}/stream")
        delta_text = "".join(item.get("text", "") for item in events)
        return {"provider_inter_delta_delay_seconds": Provider.stream_delay, "response_headers_delay_seconds": round(header_elapsed, 3), "delta_text": delta_text, "ledger_contains_scrubbing_marker": "[密钥已隐藏]" in replay, "real_incremental_delivery": header_elapsed < Provider.stream_delay / 2}


def quota(provider):
    Provider.stream_delay = 0
    Provider.json_delay = 0
    before = Provider.request_count
    env = {"MOYU_AI_BASE_URL": f"http://127.0.0.1:{provider.server_address[1]}", "MOYU_AI_MODEL": "audit-model", "MOYU_AI_DAILY_QUOTA": "1"}
    with app(env) as (base, _, _):
        first_http, first = request(base, "POST", "/api/ai/queue", {"task": "continue", "defer": True})
        second_http, second = request(base, "POST", "/api/ai/queue", {"task": "continue", "defer": True})
        processed_http, processed = request(base, "POST", "/api/ai/queue/drain", {"limit": 2})
        _, current = request(base, "GET", "/api/ai/quota")
        return {"configured_daily_quota": 1, "enqueue_http": [first_http, second_http], "drain_http": processed_http, "provider_calls": Provider.request_count - before, "used_runs": current["used_runs"], "statuses": [item["status"] for item in processed["jobs"]]}


def output_end_semantics():
    spec = importlib.util.spec_from_file_location("moyu_audit_server", ROOT / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    length_done = b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n'
    interrupted = b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
    a = module._provider_sse_result(io.BytesIO(length_done), "continue", "text")
    b = module._provider_sse_result(io.BytesIO(interrupted), "continue", "text")
    wrong_schema = module._provider_json_result({"choices": [{"message": {"content": '{"goal":null,"conflict":false,"turn":7,"scenes":"bad","foreshadowing":0}'}, "finish_reason": "stop"}]}, "json", "outline")
    return {"length_finish_plus_done_truncated": a["truncated"], "eof_without_done_truncated": b["truncated"], "eof_without_done_finish_reason": b["finish_reason"], "invalid_typed_schema_accepted": isinstance(wrong_schema["structured"], dict)}


def provider_save_lock(provider):
    Provider.json_delay = 1.25
    started_event = threading.Event()
    finished = []
    env = {"MOYU_AI_BASE_URL": f"http://127.0.0.1:{provider.server_address[1]}", "MOYU_AI_MODEL": "audit-model"}
    with app(env) as (base, _, _):
        _, state = request(base, "GET", "/api/state")
        prior_count = Provider.request_count
        def generating():
            started_event.set()
            finished.append(request(base, "POST", "/api/ai", {"task": "continue"})[0])
        thread = threading.Thread(target=generating)
        thread.start()
        started_event.wait(timeout=1)
        for _ in range(100):
            if Provider.request_count > prior_count:
                break
            time.sleep(0.01)
        state["project"]["summary"] = "并发写作探针"
        started = time.perf_counter()
        saved, _ = request(base, "PUT", "/api/state", state)
        elapsed = time.perf_counter() - started
        thread.join(timeout=5)
        return {"provider_delay_seconds": Provider.json_delay, "concurrent_save_delay_seconds": round(elapsed, 3), "save_http": saved, "ai_http": finished, "save_blocked_while_model_generates": elapsed > 1}


def queued_cancel(provider):
    Provider.json_delay = 1.25
    env = {"MOYU_AI_BASE_URL": f"http://127.0.0.1:{provider.server_address[1]}", "MOYU_AI_MODEL": "audit-model"}
    with app(env) as (base, _, _):
        prior_count = Provider.request_count
        _, queued = request(base, "POST", "/api/ai/queue", {"task": "continue"})
        for _ in range(100):
            _, detail = request(base, "GET", "/api/ai/jobs/" + queued["id"])
            if detail["status"] == "running" and Provider.request_count > prior_count:
                break
            time.sleep(0.01)
        status, canceled = request(base, "POST", "/api/ai/jobs/" + queued["id"] + "/cancel", {})
        started = time.perf_counter()
        for _ in range(180):
            _, terminal = request(base, "GET", "/api/ai/jobs/" + queued["id"])
            if terminal["status"] == "canceled":
                break
            time.sleep(0.01)
        return {"cancel_http": status, "initial_status": canceled["status"], "cancel_to_terminal_seconds": round(time.perf_counter() - started, 3), "terminal_status": terminal["status"], "provider_calls": Provider.request_count - prior_count, "provider_work_interrupted": time.perf_counter() - started < 0.2}


def main():
    provider = ThreadingHTTPServer(("127.0.0.1", port()), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    try:
        record("accepted_memory_anchor_edit", memory_anchor)
        record("deleted_chapter_json_restore", deleted_restore)
        record("ai_adoption_version_conflict", decision_conflict)
        record("rejected_memory_frontend_contract", rejection_contract)
        record("buffered_stream_and_empty_key", lambda: streaming(provider))
        record("queued_daily_quota_bypass", lambda: quota(provider))
        record("output_completion_and_schema", output_end_semantics)
        record("synchronous_ai_holds_save_lock", lambda: provider_save_lock(provider))
        record("queued_cancel_discards_after_provider_finishes", lambda: queued_cancel(provider))
    finally:
        provider.shutdown()
        provider.server_close()


if __name__ == "__main__":
    main()
