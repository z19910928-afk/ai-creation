#!/usr/bin/env python3
"""Real SQLite + local HTTP provider tests for settings, budgets and workers."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MOYU_AI_BASE_URL"] = ""
os.environ["MOYU_AI_API_KEY"] = ""
os.environ["MOYU_AI_MODEL"] = ""
import server
import moyu_settings
import moyu_backups
from moyu_provider import ProviderCanceled


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls: list[dict] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.loads(raw)
        case = self.path.split("/")[1]
        is_probe = payload.get("messages", [{}])[0].get("content") == "连接测试。只回复：连接正常"
        returned_model = payload["model"] if is_probe else "actual-fixture"
        with self.lock:
            self.calls.append({"case": case, "path": self.path, "payload": payload, "raw": raw,
                               "request_id": self.headers.get("X-Client-Request-Id"),
                               "authorization": self.headers.get("Authorization")})
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        self.send_response(401 if case == "unauthorized" else 200)
        json_cases = {"probe", "schema", "scene", "usage-secret-key", "usage-empty",
                      "usage-partial", "usage-components", "usage-conflict",
                      "usage-total-only", "usage-zero"}
        self.send_header("Content-Type", "application/json" if case in json_cases else "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            if case == "unauthorized":
                self.wfile.write(b'{"error":"provider leaked fixture-original-credential"}')
                self.wfile.flush()
            elif case in json_cases:
                text = '{"pov":[],"location":{},"goal":3,"obstacle":null,"exit":false}' if case == "schema" else "连接正常"
                if case == "scene":
                    text = json.dumps({"pov": "林岚", "location": "门外", "goal": "找到原信",
                                       "obstacle": "门被锁住", "exit": "留下门上的暗号"}, ensure_ascii=False)
                usage = {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}
                if case.startswith("usage-") and case != "usage-secret-key":
                    usage = {
                        "usage-empty": {}, "usage-partial": {"completion_tokens": 7},
                        "usage-components": {"prompt_tokens": 13, "completion_tokens": 7},
                        "usage-conflict": {"prompt_tokens": 13, "completion_tokens": 7,
                                           "total_tokens": 17},
                        "usage-total-only": {"total_tokens": 20},
                        "usage-zero": {"total_tokens": 0},
                    }[case]
                if case == "usage-secret-key":
                    usage.update({
                        "fixture-original-credential": {"nested-fixture-original-credential": "metadata"},
                        "completion_tokens_details": {"reasoning_tokens": 2,
                                                      "detail-fixture-original-credential": 2}})
                self.wfile.write(json.dumps({
                    "model": returned_model, "choices": [{"index": 0, "message": {"content": text},
                                                            "finish_reason": "stop"}],
                    "usage": usage,
                }, ensure_ascii=False).encode())
                self.wfile.flush()
            else:
                self.wfile.write(("data: " + json.dumps({
                    "model": returned_model, "choices": [{"index": 0,
                        "delta": {"content": "连接" if is_probe else "先到正文"},
                        "finish_reason": "" if case == "empty-finish" else None}],
                }, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
                time.sleep(0.65 if case == "slow" else 0.25)
                if case == "eof":
                    return
                final_usage = {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}
                if case == "usage-stream-regress":
                    self.wfile.write(
                        b'data: {"choices":[],"usage":{"prompt_tokens":13,"completion_tokens":7,"total_tokens":20}}\n\n')
                    final_usage = {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
                self.wfile.write(
                    ("data: " + json.dumps({"choices": [{"index": 0,
                        "delta": {"content": "正常" if is_probe else "后到正文"},
                        "finish_reason": "stop"}]}, ensure_ascii=False) + "\n\n").encode()
                    + ("data: " + json.dumps({"choices": [], "usage": final_usage}) + "\n\n").encode()
                    + b"data: [DONE]\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self.lock:
                type(self).active -= 1

    def log_message(self, *_args: object) -> None:
        return


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.provider.daemon_threads = True
        cls.provider_thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.provider_thread.start()
        cls.base = f"http://127.0.0.1:{cls.provider.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.provider_thread.join(timeout=2)
        if server._AI_EXECUTOR is not None:
            server._AI_EXECUTOR.shutdown(wait=True)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="moyu-provider-integration-")
        self.root = Path(self.tmp.name)
        server.DB_PATH = self.root / "book.sqlite3"
        os.environ["MOYU_CREDENTIALS_DIR"] = str(self.root / "private-keys")
        server.init_db()
        with server.connect() as conn:
            server.ensure_seed(conn)
            self.chapter_id = conn.execute(
                "SELECT id FROM document_node WHERE kind='chapter' ORDER BY rowid LIMIT 1").fetchone()[0]
        self.project = server.PROJECT_ID
        self.branch = server.BRANCH_ID
        self.jobs = []

    def tearDown(self) -> None:
        if hasattr(self, "api"):
            self.api.shutdown()
            self.api.server_close()
            self.api_thread.join(timeout=2)
        for thread in self.jobs:
            thread.join(timeout=5)
        self.tmp.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        if not hasattr(self, "api"):
            class AppHandler(server.Handler):
                def log_message(self, *_args: object) -> None:
                    return
            self.api = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
            self.api.daemon_threads = True
            self.api_thread = threading.Thread(target=self.api.serve_forever, daemon=True)
            self.api_thread.start()
        raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.api.server_port}" + path, data=raw, method=method,
            headers={"Content-Type": "application/json", "X-Moyu-Project": self.project},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def settings(self, case: str = "normal", *, limit: int = 0,
                 key: str = "fixture-original-credential", model: str = "writer-fixture",
                 temperature: float = 0, max_tokens: int = 3000) -> None:
        with server.connect() as conn:
            moyu_settings.save_settings(conn, self.project, {
                "base_url": self.base + "/" + case, "model": model, "api_key": key,
                "models": {"continue": model}, "styles": {"tone": "克制", "instructions": "不要擅自改人物事实"},
                "parameters": {"temperature": temperature, "max_tokens": max_tokens},
                "daily_limit": limit,
            })

    def enqueue(self, task: str = "continue", response_format: str = "text") -> dict:
        with server.connect() as conn:
            chapter_id = conn.execute("SELECT id FROM document_node WHERE kind='chapter' ORDER BY rowid LIMIT 1").fetchone()[0]
            context, _chapter = server.ai_context(conn, chapter_id)
            context["_source"] = {"project_id": self.project, "branch_id": self.branch,
                                   "chapter_id": context["current_chapter"]["id"],
                                   "revision_id": "fixture-source-revision", "chapter_hash": "fixture-source-hash"}
            return server.create_ai_job(conn, task, context, "测试要求", response_format)

    def wait_job(self, job: dict) -> dict:
        for _ in range(200):
            status, detail = self.request("GET", "/api/ai/jobs/" + job["id"])
            self.assertEqual(status, 200)
            if detail["status"] not in {"queued", "running"}:
                return detail
            time.sleep(0.01)
        self.fail("queued HTTP request never completed")

    def canceled_http_job(self) -> dict:
        status, job = self.request("POST", "/api/ai/queue", {"task": "continue", "defer": True})
        self.assertEqual(status, 202, job)
        status, canceled = self.request("POST", f"/api/ai/jobs/{job['id']}/cancel", {})
        self.assertEqual((status, canceled["status"]), (200, "canceled"))
        return job

    def test_settings_preserve_thinking_and_reject_invalid_or_conflicting_objects(self) -> None:
        self.settings()
        parameters = {"temperature": 0, "max_tokens": 3000, "thinking": {"type": "disabled"}}
        status, saved = self.request("PUT", "/api/ai/settings", {"parameters": parameters})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["settings"]["parameters"], parameters)
        before = len(Handler.calls)
        for invalid in (None, "disabled", {}, {"type": []},
                        {"type": "disabled", "budget_tokens": 1000},
                        {"type": "enabled", "enabled": True}):
            with self.subTest(thinking=invalid):
                status, rejected = self.request("PUT", "/api/ai/settings", {
                    "parameters": {**parameters, "thinking": invalid}})
                self.assertEqual(status, 400, rejected)
        for mode, effort in (("enabled", "none"), ("disabled", "low")):
            with self.subTest(mode=mode, effort=effort):
                status, rejected = self.request("PUT", "/api/ai/settings", {
                    "parameters": {**parameters, "thinking": {"type": mode},
                                   "reasoning_effort": effort}})
                self.assertEqual(status, 400, rejected)
        self.assertEqual(len(Handler.calls), before)
        with server.connect() as conn:
            public = moyu_settings.public_settings(conn, self.project)
            config = moyu_settings.provider_settings(conn, self.project)
            self.assertEqual(public["parameters"], parameters)
            self.assertEqual(config["parameters"]["thinking"], {"type": "disabled"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_run").fetchone()[0], 0)

    def test_recovered_queued_job_keeps_original_thinking_after_settings_change(self) -> None:
        self.settings(model="original-fixture")
        status, saved = self.request("PUT", "/api/ai/settings", {
            "parameters": {"temperature": 0, "max_tokens": 3000,
                           "thinking": {"type": "disabled"}}})
        self.assertEqual(status, 200, saved)
        status, job = self.request("POST", "/api/ai/queue", {"task": "continue", "defer": True})
        self.assertEqual(status, 202, job)
        self.settings(model="replacement-fixture", temperature=1)
        status, saved = self.request("PUT", "/api/ai/settings", {
            "parameters": {"temperature": 1, "max_tokens": 9000,
                           "thinking": {"type": "enabled"}, "reasoning_effort": "low"}})
        self.assertEqual(status, 200, saved)
        before = len(Handler.calls)
        self.assertEqual(server.recover_ai_jobs(), [job["id"]])
        self.assertEqual(len(Handler.calls), before)
        status, completed = self.request("POST", f"/api/ai/jobs/{job['id']}/process", {})
        self.assertEqual((status, completed["status"]), (200, "success"), completed)
        actual = Handler.calls[-1]["payload"]
        self.assertEqual(actual["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", actual)
        self.assertEqual(actual["model"], "original-fixture")
        self.assertEqual(actual["max_tokens"], 3000)
        with server.connect() as conn:
            run = conn.execute("SELECT sampling_json,consent_json FROM ai_run WHERE id=?",
                               (job["ai_run_id"],)).fetchone()
        self.assertEqual(json.loads(run["sampling_json"])["thinking"], {"type": "disabled"})
        self.assertEqual(json.loads(run["consent_json"])["request_audit"]["payload"], actual)

    def test_saved_output_limit_reaches_both_http_generation_routes_and_task_models(self) -> None:
        self.settings(model="default-fixture")
        status, saved = self.request("PUT", "/api/ai/settings", {
            "parameters": {"temperature": 0, "max_tokens": 5000},
            "models": {"continue": "continue-fixture", "polish": "polish-fixture"},
        })
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["settings"]["parameters"]["max_tokens"], 5000)
        status, preview = self.request("POST", "/api/ai/prompt-preview", {
            "task": "continue", "chapter_id": self.chapter_id})
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["request"]["max_tokens"], 5000)
        status, generated = self.request("POST", "/api/ai", {"task": "continue"})
        self.assertEqual(status, 200, generated)
        self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], 5000)
        self.assertEqual(Handler.calls[-1]["payload"]["model"], "continue-fixture")
        status, job = self.request("POST", "/api/ai/queue", {"task": "polish", "defer": True})
        self.assertEqual(status, 202, job)
        self.assertEqual(job["max_tokens"], 5000)
        status, completed = self.request("POST", f"/api/ai/jobs/{job['id']}/process", {})
        self.assertEqual((status, completed["status"]), (200, "success"))
        self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], 5000)
        self.assertEqual(Handler.calls[-1]["payload"]["model"], "polish-fixture")
        status, explicit = self.request("POST", "/api/ai", {"task": "continue", "max_tokens": 7500})
        self.assertEqual(status, 200, explicit)
        self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], 7500)

    def test_saved_and_explicit_output_boundaries_use_the_same_range(self) -> None:
        self.settings()
        for maximum in (1, 128_000):
            with self.subTest(maximum=maximum):
                status, saved = self.request("PUT", "/api/ai/settings", {
                    "parameters": {"temperature": 0, "max_tokens": maximum}})
                self.assertEqual(status, 200, saved)
                status, generated = self.request("POST", "/api/ai", {"task": "continue"})
                self.assertEqual(status, 200, generated)
                self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], maximum)
                status, generated = self.request("POST", "/api/ai", {
                    "task": "continue", "max_tokens": maximum})
                self.assertEqual(status, 200, generated)
                self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], maximum)

    def test_invalid_explicit_output_limits_never_default_reserve_or_dispatch(self) -> None:
        self.settings(max_tokens=5000, limit=1)
        before = len(Handler.calls)
        invalid = (0, -1, None, False, True, 1.5, 5000.0, "5000", 128_001)
        for path in ("/api/ai", "/api/ai/queue", "/api/ai/prompt-preview"):
            for maximum in invalid:
                with self.subTest(path=path, maximum=maximum):
                    status, rejected = self.request("POST", path, {
                        "task": "continue", "chapter_id": self.chapter_id,
                        "max_tokens": maximum, "defer": True})
                    self.assertEqual(status, 400, rejected)
                    self.assertIn("整数", rejected["error"])
        self.assertEqual(len(Handler.calls), before)
        with server.connect() as conn:
            for table in ("app_ai_job", "ai_run", "app_quota_reservation"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(server.ai_quota_snapshot(conn)["remaining_runs"], 1)

    def test_unconfigured_output_default_remains_3000(self) -> None:
        status, job = self.request("POST", "/api/ai/queue", {"task": "continue", "defer": True})
        self.assertEqual(status, 202, job)
        self.assertEqual(job["max_tokens"], 3000)
        status, generated = self.request("POST", "/api/ai", {"task": "continue"})
        self.assertEqual(status, 200, generated)
        self.assertEqual(generated["mode"], "offline")
        with server.connect() as conn:
            self.assertEqual([row[0] for row in conn.execute("SELECT max_tokens FROM app_ai_job")],
                             [3000, 3000])
        with patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": self.base + "/normal", "MOYU_AI_MODEL": "environment-fixture",
        }):
            status, generated = self.request("POST", "/api/ai", {"task": "continue"})
            self.assertEqual(status, 200, generated)
            self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], 3000)

    def test_confirmed_assistant_uses_saved_output_limit_and_freezes_it(self) -> None:
        self.settings(max_tokens=5000)
        before = len(Handler.calls)
        status, preview = self.request("POST", "/api/assistant/query", {
            "query": "请续写当前章", "chapter_id": self.chapter_id})
        self.assertEqual(status, 200, preview)
        status, confirmed = self.request("POST", "/api/assistant/confirm", {
            "token": preview["proposal"]["token"], "confirm": True, "defer": True})
        self.assertEqual(status, 202, confirmed)
        self.assertEqual(confirmed["job"]["max_tokens"], 5000)
        self.assertEqual(len(Handler.calls), before)
        self.settings(max_tokens=9000)
        status, completed = self.request("POST", f"/api/ai/jobs/{confirmed['job']['id']}/process", {})
        self.assertEqual((status, completed["status"]), (200, "success"))
        self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], 5000)

    def test_deferred_http_job_keeps_original_output_limit_after_settings_change(self) -> None:
        self.settings(max_tokens=5000, model="original-fixture")
        status, job = self.request("POST", "/api/ai/queue", {"task": "continue", "defer": True})
        self.assertEqual(status, 202, job)
        self.settings(max_tokens=8000, model="replacement-fixture", temperature=1)
        status, completed = self.request("POST", f"/api/ai/jobs/{job['id']}/process", {})
        self.assertEqual((status, completed["status"]), (200, "success"))
        self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], 5000)
        self.assertEqual(Handler.calls[-1]["payload"]["model"], "original-fixture")
        self.assertEqual(Handler.calls[-1]["payload"]["temperature"], 0)
        with server.connect() as conn:
            sampling = conn.execute("SELECT sampling_json FROM ai_run WHERE id=?",
                                    (job["ai_run_id"],)).fetchone()[0]
            self.assertEqual(json.loads(sampling)["max_tokens"], 5000)

    def test_http_job_retry_keeps_original_limit_and_rejects_explicit_change(self) -> None:
        self.settings(max_tokens=5000)
        job = self.canceled_http_job()
        self.settings(max_tokens=9000)
        before = len(Handler.calls)
        for maximum, expected in ((0, 400), (None, 400), (False, 400), (5000.0, 400), (9000, 409)):
            with self.subTest(maximum=maximum):
                status, rejected = self.request("POST", f"/api/ai/jobs/{job['id']}/retry",
                                                {"max_tokens": maximum})
                self.assertEqual(status, expected, rejected)
        self.assertEqual(len(Handler.calls), before)
        status, retried = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
        self.assertEqual(status, 202, retried)
        completed = self.wait_job(retried)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(completed["max_tokens"], 5000)
        self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], 5000)
        self.assertEqual(retried["retry_of"], job["ai_run_id"])

    def test_legacy_run_retry_uses_durable_job_quota_and_original_limit(self) -> None:
        self.settings(max_tokens=5000, limit=2)
        job = self.canceled_http_job()
        self.settings(max_tokens=9000, limit=2)
        path = f"/api/ai/runs/{job['ai_run_id']}/retry"
        before = len(Handler.calls)
        for payload, expected in (({"max_tokens": 0}, 400), ({"max_tokens": 9000}, 409),
                                  ({"task": "polish"}, 409)):
            status, rejected = self.request("POST", path, payload)
            self.assertEqual(status, expected, rejected)
        self.assertEqual(len(Handler.calls), before)
        status, generated = self.request("POST", path, {})
        self.assertEqual(status, 200, generated)
        self.assertEqual(generated["text"], "先到正文后到正文")
        self.assertEqual(generated["retry_of"], job["ai_run_id"])
        self.assertTrue(generated["stream"]["replayable"])
        self.assertEqual(Handler.calls[-1]["payload"]["max_tokens"], 5000)
        with server.connect() as conn:
            retried = conn.execute("SELECT * FROM app_ai_job WHERE ai_run_id=?",
                                  (generated["ai_run_id"],)).fetchone()
            self.assertEqual(retried["max_tokens"], 5000)
            self.assertEqual(retried["retry_of"], job["ai_run_id"])
            self.assertEqual(json.loads(retried["context_json"])["_source"], job["source"])
            self.assertEqual(conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                         (generated["ai_run_id"],)).fetchone()[0], "consumed")
            self.assertEqual(server.ai_quota_snapshot(conn)["used_runs"], 1)

    def test_both_retry_routes_refuse_unknown_billing_completed_and_legacy_requests(self) -> None:
        self.settings("eof")
        job = self.enqueue()
        self.assertEqual(server.process_ai_job(job["id"])["status"], "failed")
        before = len(Handler.calls)
        for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
            status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
            self.assertEqual(status, 409, rejected)
            self.assertIn("计费", rejected["error"])
        self.assertEqual(len(Handler.calls), before)
        self.settings()
        completed_job = self.enqueue()
        self.assertEqual(server.process_ai_job(completed_job["id"])["status"], "success")
        before = len(Handler.calls)
        for kind, identifier in (("jobs", completed_job["id"]), ("runs", completed_job["ai_run_id"])):
            status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
            self.assertEqual(status, 409, rejected)
        with server.connect() as conn:
            context, _ = server.ai_context(conn, None)
            legacy = server.record_ai_run(conn, "continue", context, "", {
                "mode": "offline", "text": "", "safe_to_apply": False,
                "billing_state": "not_dispatched",
            }, server.now(), 0, status="failed")
        status, rejected = self.request("POST", f"/api/ai/runs/{legacy}/retry", {})
        self.assertEqual(status, 409, rejected)
        self.assertIn("原任务资料", rejected["error"])

    def test_partial_and_conflicting_usage_persist_unknown_quota_and_block_both_retry_routes(self) -> None:
        for case, usage_state, lower in (("usage-empty", "unknown", 0),
                                         ("usage-partial", "partial", 7),
                                         ("usage-conflict", "inconsistent", 20)):
            with self.subTest(case=case):
                self.settings(case)
                job = self.enqueue()
                completed = server.process_ai_job(job["id"])
                self.assertEqual(completed["status"], "success")
                result = completed["result"]
                self.assertEqual(result["usage_state"], usage_state)
                self.assertEqual(result["billing_state"], "unknown")
                self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], lower)
                with server.connect() as conn:
                    reservation = conn.execute(
                        "SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                        (job["ai_run_id"],)).fetchone()
                    self.assertEqual(reservation["status"], "unknown")
                    run = conn.execute("SELECT token_json,consent_json FROM ai_run WHERE id=?",
                                       (job["ai_run_id"],)).fetchone()
                    self.assertEqual(json.loads(run["token_json"])["usage"], result["usage"])
                    self.assertEqual(json.loads(run["token_json"])["usage_state"], usage_state)
                    self.assertEqual(json.loads(run["token_json"])["usage_accounting"],
                                     result["usage_accounting"])
                    self.assertEqual(json.loads(run["consent_json"])["billing_state"], "unknown")
                before = len(Handler.calls)
                for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                    status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
                    self.assertEqual(status, 409, rejected)
                    self.assertIn("计费", rejected["error"])
                self.assertEqual(len(Handler.calls), before)
        with server.connect() as conn:
            quota = server.ai_quota_snapshot(conn)
            self.assertEqual(quota["used_runs"], 3)
            self.assertEqual(quota["used_tokens"], 0)
            self.assertEqual(quota["reported_tokens_lower_bound"], 27)
            self.assertEqual(quota["usage_counts"],
                             {"reported": 0, "partial": 1, "inconsistent": 1, "unknown": 1})
            self.assertEqual(quota["usage_state"], "inconsistent")
            self.assertFalse(quota["usage_complete"])
            self.assertGreater(quota["estimated_tokens"], 0)

    def test_derived_complete_usage_consumes_quota_without_changing_raw_counters(self) -> None:
        self.settings("usage-components", limit=1)
        job = self.enqueue()
        result = server.process_ai_job(job["id"])["result"]
        self.assertEqual(result["usage"], {"prompt_tokens": 13, "completion_tokens": 7})
        self.assertEqual(result["usage_accounting"]["total_tokens"], 20)
        self.assertEqual(result["usage_accounting"]["source"], "derived_from_components")
        self.assertEqual(result["billing_state"], "usage_reported")
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                         (job["ai_run_id"],)).fetchone()[0], "consumed")
            tokens = json.loads(conn.execute("SELECT token_json FROM ai_run WHERE id=?",
                                            (job["ai_run_id"],)).fetchone()[0])
            self.assertEqual(tokens["usage"], result["usage"])
            self.assertEqual(tokens["usage_accounting"], result["usage_accounting"])
            quota = server.ai_quota_snapshot(conn)
            self.assertEqual(quota["remaining_runs"], 0)
            self.assertEqual(quota["used_tokens"], 20)
            self.assertEqual(quota["reported_tokens_lower_bound"], 20)
            self.assertEqual(quota["estimated_tokens"], tokens["estimated_total_tokens"])
            self.assertEqual(quota["usage_counts"],
                             {"reported": 1, "partial": 0, "inconsistent": 0, "unknown": 0})
            self.assertEqual(quota["usage_state"], "reported")
            self.assertTrue(quota["usage_complete"])

    def test_probe_with_partial_usage_is_healthy_but_bill_and_quota_remain_unknown(self) -> None:
        self.settings("usage-partial", limit=1)
        with server.connect() as conn:
            response = moyu_settings.probe_settings(conn, self.project)
            self.assertTrue(response["probe"]["success"])
            self.assertEqual(response["probe"]["usage"], {"completion_tokens": 7})
            self.assertEqual(response["probe"]["billing_state"], "unknown")
            run = conn.execute("SELECT id,token_json FROM ai_run ORDER BY rowid DESC LIMIT 1").fetchone()
            tokens = json.loads(run["token_json"])
            self.assertEqual(tokens["usage_state"], "partial")
            self.assertEqual(tokens["usage_accounting"]["reported_tokens_lower_bound"], 7)
            self.assertIsNone(tokens["usage_accounting"]["total_tokens"])
            self.assertEqual(conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                         (run["id"],)).fetchone()[0], "unknown")
            quota = server.ai_quota_snapshot(conn)
            self.assertEqual(quota["remaining_runs"], 0)
            self.assertEqual(quota["used_tokens"], 0)
            self.assertEqual(quota["reported_tokens_lower_bound"], 7)
            self.assertEqual(quota["estimated_tokens"], 0)
            self.assertEqual(quota["usage_counts"]["partial"], 1)
            self.assertFalse(quota["usage_complete"])

    def test_total_only_and_zero_usage_remain_exact_without_estimate_fallback(self) -> None:
        for case, total in (("usage-total-only", 20), ("usage-zero", 0)):
            with self.subTest(case=case):
                self.settings(case)
                job = self.enqueue()
                result = server.process_ai_job(job["id"])["result"]
                self.assertEqual(result["usage_accounting"]["total_tokens"], total)
                with server.connect() as conn:
                    tokens = json.loads(conn.execute("SELECT token_json FROM ai_run WHERE id=?",
                                                    (job["ai_run_id"],)).fetchone()[0])
                    self.assertEqual(tokens["usage"], {"total_tokens": total})
                    self.assertGreater(tokens["estimated_total_tokens"], 0)
        status, quota = self.request("GET", "/api/ai/quota")
        self.assertEqual(status, 200)
        self.assertEqual(quota["used_runs"], 2)
        self.assertEqual(quota["used_tokens"], 20)
        self.assertEqual(quota["reported_tokens_lower_bound"], 20)
        self.assertEqual(quota["usage_counts"]["reported"], 2)
        self.assertTrue(quota["usage_complete"])
        self.assertEqual(quota["usage_state"], "reported")

    def test_stream_usage_high_water_mark_survives_queued_run_persistence(self) -> None:
        self.settings("usage-stream-regress", limit=1)
        job = self.enqueue()
        result = server.process_ai_job(job["id"])["result"]
        self.assertEqual(result["usage"], {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12})
        self.assertEqual(result["usage_state"], "inconsistent")
        self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], 20)
        with server.connect() as conn:
            tokens = json.loads(conn.execute("SELECT token_json FROM ai_run WHERE id=?",
                                            (job["ai_run_id"],)).fetchone()[0])
            self.assertEqual(tokens["usage_accounting"], result["usage_accounting"])
            quota = server.ai_quota_snapshot(conn)
            self.assertEqual(quota["remaining_runs"], 0)
            self.assertEqual(quota["used_tokens"], 0)
            self.assertEqual(quota["reported_tokens_lower_bound"], 20)
            self.assertEqual(quota["usage_counts"]["inconsistent"], 1)
            self.assertFalse(quota["usage_complete"])

    def test_probe_stream_usage_high_water_mark_survives_without_job_result(self) -> None:
        self.settings("usage-stream-regress", limit=1)
        with server.connect() as conn:
            response = moyu_settings.probe_settings(conn, self.project)
            self.assertTrue(response["probe"]["success"])
            self.assertEqual(response["probe"]["billing_state"], "unknown")
            tokens = json.loads(conn.execute("SELECT token_json FROM ai_run ORDER BY rowid DESC LIMIT 1").fetchone()[0])
            self.assertEqual(tokens["usage_state"], "inconsistent")
            self.assertEqual(tokens["usage"]["total_tokens"], 12)
            self.assertEqual(tokens["usage_accounting"]["reported_tokens_lower_bound"], 20)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_job").fetchone()[0], 0)
            quota = server.ai_quota_snapshot(conn)
            self.assertEqual(quota["remaining_runs"], 0)
            self.assertEqual(quota["used_tokens"], 0)
            self.assertEqual(quota["reported_tokens_lower_bound"], 20)
            self.assertEqual(quota["usage_counts"]["inconsistent"], 1)

    def test_legacy_uncertain_usage_never_upgrades_from_coherent_raw_total(self) -> None:
        cases = (
            ("inconsistent", {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
             "inconsistent", 12),
            ("partial", {"total_tokens": 20}, "partial", 20),
            ("unknown", {"total_tokens": 20}, "partial", 20),
            ("reported", {"prompt_tokens": 13, "completion_tokens": 7}, "reported", 20),
            ("reported", {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 17},
             "inconsistent", 20),
        )
        for saved_state, raw, expected_state, lower in cases:
            with self.subTest(saved_state=saved_state, raw=raw):
                with server.connect() as conn:
                    run_id = server.record_ai_run(conn, "continue", {}, "", {
                        "mode": "provider", "model": "legacy-fixture", "text": "正文",
                    }, server.now(), 0)
                    legacy = {"usage": raw, "usage_state": saved_state, "estimated_total_tokens": 99}
                    conn.execute("UPDATE ai_run SET token_json=? WHERE id=?",
                                 (json.dumps(legacy), run_id))
                    quota = server.ai_quota_snapshot(conn)
                    self.assertEqual(quota["used_tokens"], 20 if expected_state == "reported" else 0)
                    self.assertEqual(quota["reported_tokens_lower_bound"], lower)
                    self.assertEqual(quota["usage_counts"][expected_state], 1)
                    self.assertEqual(quota["usage_state"], expected_state)
                    self.assertEqual(quota["estimated_tokens"], 99)
                    self.assertEqual(quota["usage_complete"], expected_state == "reported")
                    conn.execute("UPDATE ai_run SET status='failed' WHERE id=?", (run_id,))

    def test_saved_usage_metadata_is_revalidated_without_losing_known_lower_bound(self) -> None:
        cases = (
            ({"state": "inconsistent", "total_tokens": None, "reported_tokens_lower_bound": 20,
              "source": "inconsistent_provider_counters"}, "inconsistent", "inconsistent", 20),
            ({"state": "reported", "total_tokens": 12, "reported_tokens_lower_bound": 12,
              "source": "provider_reported"}, "partial", "partial", 12),
            ({"state": "reported", "total_tokens": 12, "reported_tokens_lower_bound": 20,
              "source": "provider_reported"}, "reported", "inconsistent", 20),
            ({"state": "reported", "total_tokens": 12, "reported_tokens_lower_bound": 12,
              "source": "provider_reported"}, [], "inconsistent", 12),
        )
        for metadata, saved_state, expected_state, lower in cases:
            with self.subTest(metadata=metadata, saved_state=saved_state):
                _, _, _, _, tokens, _ = server.ai_result_metrics({}, "", {
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
                    "usage_accounting": metadata, "usage_state": saved_state,
                })
                self.assertEqual(tokens["usage_state"], expected_state)
                self.assertEqual(tokens["usage_accounting"]["state"], expected_state)
                self.assertIsNone(tokens["usage_accounting"]["total_tokens"])
                self.assertEqual(tokens["usage_accounting"]["reported_tokens_lower_bound"], lower)

    def test_legacy_retry_cannot_bypass_daily_reservation_limit(self) -> None:
        self.settings(max_tokens=5000, limit=1)
        job = self.canceled_http_job()
        status, generated = self.request("POST", "/api/ai", {"task": "continue"})
        self.assertEqual(status, 200, generated)
        before = len(Handler.calls)
        for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
            status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
            self.assertEqual(status, 400, rejected)
            self.assertIn("今日生成上限", rejected["error"])
        self.assertEqual(len(Handler.calls), before)
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_job").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_run").fetchone()[0], 2)
            self.assertEqual(server.ai_quota_snapshot(conn)["used_runs"], 1)

    def test_empty_stream_finish_marker_completes_only_after_stop_and_done(self) -> None:
        self.settings("empty-finish")
        completed = server.process_ai_job(self.enqueue()["id"])
        self.assertEqual(completed["status"], "success")
        self.assertTrue(completed["result"]["safe_to_apply"])
        self.assertEqual(completed["result"]["finish_reason"], "stop")
        self.assertEqual(completed["result"]["text"], "先到正文后到正文")
        self.assertEqual(
            completed["result"]["protocol_observation"]["events"][0]["choices"][0]["finish_reason"], "")

    def test_original_retry_preserves_exact_body_endpoint_and_private_credential(self) -> None:
        self.settings(model="original-default", max_tokens=5000)
        status, saved = self.request("PUT", "/api/ai/settings", {
            "models": {"continue": "original-purpose-model", "polish": "original-polish"},
            "styles": {"tone": "原始冷静文风", "instructions": "保留原信内容"},
            "parameters": {"temperature": 0, "top_p": 0.7, "presence_penalty": -0.3,
                           "frequency_penalty": 0.4, "seed": 17, "stop": ["END"],
                           "reasoning_effort": "low", "thinking": {"type": "enabled"},
                           "max_tokens": 5000},
        })
        self.assertEqual(status, 200, saved)
        job = self.canceled_http_job()
        with server.connect() as conn:
            original_context = conn.execute("SELECT context_json FROM app_ai_job WHERE id=?",
                                            (job["id"],)).fetchone()[0]
            original = json.loads(conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                              (job["ai_run_id"],)).fetchone()[0])
            old_payload = original["request_audit"]["payload"]
            old_hash = original["request_audit"]["request_sha256"]
        self.settings(model="replacement-default", max_tokens=9000, temperature=1.7,
                      key="fixture-new-credential")
        status, saved = self.request("PUT", "/api/ai/settings", {
            "models": {"continue": "replacement-purpose-model", "polish": "replacement-polish"},
            "styles": {"tone": "新的夸张文风", "instructions": "加入新的秘密"},
            "parameters": {"temperature": 1.7, "top_p": 0.3, "presence_penalty": 1,
                           "frequency_penalty": 1.2, "seed": 99, "stop": ["NEW"],
                           "reasoning_effort": "none", "thinking": {"type": "disabled"},
                           "max_tokens": 9000},
        })
        self.assertEqual(status, 200, saved)
        before = len(Handler.calls)
        status, retried = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {"mode": "original"})
        self.assertEqual(status, 202, retried)
        completed = self.wait_job(retried)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Handler.calls), before + 1)
        actual = Handler.calls[-1]
        self.assertEqual(actual["payload"], old_payload)
        self.assertEqual(actual["raw"], server.json_text(old_payload).encode())
        self.assertEqual(actual["path"], "/normal/chat/completions")
        self.assertEqual(actual["authorization"], "Bearer fixture-original-credential")
        self.assertEqual(actual["request_id"], retried["ai_run_id"])
        self.assertNotIn("新的夸张文风", actual["raw"].decode())
        with server.connect() as conn:
            saved_job = conn.execute("SELECT context_json FROM app_ai_job WHERE id=?",
                                    (retried["id"],)).fetchone()[0]
            audit = json.loads(conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                           (retried["ai_run_id"],)).fetchone()[0])
            database_dump = "\n".join(conn.iterdump())
        self.assertEqual(saved_job, original_context)
        self.assertEqual(audit["provider_config"], original["provider_config"])
        self.assertEqual(audit["request_audit"]["request_sha256"], old_hash)
        self.assertEqual(audit["request_audit"]["endpoint"], original["request_audit"]["endpoint"])
        self.assertNotIn("fixture-original-credential", database_dump)
        self.assertNotIn("fixture-new-credential", database_dump)

    def test_original_json_retry_preserves_nonstream_schema_and_purpose_model(self) -> None:
        self.settings("scene", model="original-default", max_tokens=5000)
        with server.connect() as conn:
            moyu_settings.save_settings(conn, self.project, {
                "models": {"scene": "original-scene-model"}})
            config = moyu_settings.provider_settings(conn, self.project)
            context, _ = server.ai_context(conn, self.chapter_id)
            chapter = next(item for item in server.chapter_rows(conn) if item["id"] == self.chapter_id)
            context["_source"] = server.candidate_source(conn, "scene", chapter)
            payload = server.ai_prompt_preview(
                "scene", context, "原场景要求", "json", 5000, config, stream=False)["request"]
            job = server.create_ai_job(
                conn, "scene", context, "原场景要求", "json", 5000,
                provider_config=config, frozen_request=payload)
        self.assertEqual(self.request("POST", f"/api/ai/jobs/{job['id']}/cancel", {})[0], 200)
        self.settings("scene", model="replacement-default", max_tokens=9000, temperature=1.6)
        status, rejected = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/retry",
                                        {"stream": True})
        self.assertEqual(status, 400, rejected)
        status, generated = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/retry", {})
        self.assertEqual(status, 200, generated)
        self.assertEqual(Handler.calls[-1]["payload"], payload)
        self.assertFalse(Handler.calls[-1]["payload"]["stream"])
        self.assertEqual(Handler.calls[-1]["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(Handler.calls[-1]["payload"]["model"], "original-scene-model")
        self.assertEqual(generated["structured"]["pov"], "林岚")

    def test_endpoint_change_refuses_retry_before_reading_old_or_new_credential(self) -> None:
        self.settings()
        job = self.canceled_http_job()
        self.settings("probe", key="fixture-new-credential")
        before = len(Handler.calls)
        with patch("moyu_settings._read_key", side_effect=AssertionError("credential read before endpoint check")):
            for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
                self.assertEqual(status, 409, rejected)
                self.assertIn("地址", rejected["error"])
                self.assertIn("新任务", rejected["error"])
        self.assertEqual(len(Handler.calls), before)
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_job").fetchone()[0], 1)

    def test_deleted_or_cleared_settings_refuse_original_retry(self) -> None:
        self.settings()
        job = self.canceled_http_job()
        with server.connect() as conn:
            conn.execute("DELETE FROM app_model_settings WHERE project_id=?", (self.project,))
        before = len(Handler.calls)
        with patch("moyu_settings._read_key", side_effect=AssertionError("deleted settings read a credential")):
            for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
                self.assertEqual(status, 409, rejected)
                self.assertIn("已删除", rejected["error"])
        self.settings()
        status, cleared = self.request("PUT", "/api/ai/settings", {"clear_api_key": True})
        self.assertEqual(status, 200, cleared)
        with patch("moyu_settings._read_key", side_effect=AssertionError("cleared settings read a credential")):
            for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
                self.assertEqual(status, 409, rejected)
                self.assertIn("已清除", rejected["error"])
        self.assertEqual(len(Handler.calls), before)

    def test_retry_does_not_replace_missing_original_key_with_new_key(self) -> None:
        self.settings()
        job = self.canceled_http_job()
        original_path = next((self.root / "private-keys").glob("*.json"))
        self.settings(key="fixture-new-credential")
        original_path.unlink()
        before = len(Handler.calls)
        for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
            status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
            self.assertEqual(status, 409, rejected)
            self.assertIn("密钥文件缺失", rejected["error"])
        self.assertEqual(len(Handler.calls), before)

    def test_endpoint_change_after_retry_submission_fails_without_dispatch_or_charge(self) -> None:
        self.settings(limit=1)
        job = self.canceled_http_job()
        with patch.object(server, "start_ai_worker", return_value=False):
            status, retried = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
        self.assertEqual(status, 202, retried)
        self.settings("probe", limit=1, key="fixture-new-credential")
        before = len(Handler.calls)
        status, failed = self.request("POST", f"/api/ai/jobs/{retried['id']}/process", {})
        self.assertEqual((status, failed["status"]), (200, "failed"))
        self.assertIn("地址", failed["error"])
        self.assertEqual(failed["result"]["billing_state"], "not_dispatched")
        self.assertEqual(len(Handler.calls), before)
        with server.connect() as conn:
            self.assertEqual(server.ai_quota_snapshot(conn)["remaining_runs"], 1)
            self.assertEqual(conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                         (retried["ai_run_id"],)).fetchone()[0], "released")

    def test_environment_endpoint_or_identity_change_refuses_original_retry(self) -> None:
        before = len(Handler.calls)
        with patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": self.base + "/normal", "MOYU_AI_MODEL": "original-environment",
            "MOYU_AI_API_KEY": "fixture-environment-original",
        }):
            job = self.canceled_http_job()
            os.environ["MOYU_AI_BASE_URL"] = self.base + "/probe"
            os.environ["MOYU_AI_API_KEY"] = "fixture-environment-new"
            status, rejected = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
            self.assertEqual(status, 409, rejected)
            self.assertIn("地址", rejected["error"])
            os.environ["MOYU_AI_BASE_URL"] = self.base + "/normal"
            status, rejected = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
            self.assertEqual(status, 409, rejected)
            self.assertIn("密钥已变化", rejected["error"])
        self.assertEqual(len(Handler.calls), before)

    def test_original_retry_refuses_missing_or_tampered_request_record(self) -> None:
        self.settings()
        job = self.canceled_http_job()
        before = len(Handler.calls)
        with server.connect() as conn:
            consent = json.loads(conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                             (job["ai_run_id"],)).fetchone()[0])
            consent["request_audit"]["payload"]["temperature"] = 1.7
            conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?",
                         (server.json_text(consent), job["ai_run_id"]))
        status, rejected = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
        self.assertEqual(status, 409, rejected)
        self.assertIn("校验失败", rejected["error"])
        with server.connect() as conn:
            consent["request_audit"] = {}
            conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?",
                         (server.json_text(consent), job["ai_run_id"]))
        status, rejected = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/retry", {})
        self.assertEqual(status, 409, rejected)
        self.assertIn("完整请求缺失", rejected["error"])
        self.assertEqual(len(Handler.calls), before)

    def wait_chunks(self, job: dict, minimum: int = 1) -> None:
        for _ in range(100):
            with server.connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM app_ai_chunk WHERE ai_run_id=?", (job["ai_run_id"],)).fetchone()[0]
            if count >= minimum:
                return
            time.sleep(0.01)
        self.fail("first provider chunk never persisted")

    def process_thread(self, job: dict) -> tuple[threading.Thread, list[dict]]:
        outputs = []
        def work() -> None:
            outputs.append(server.process_ai_job(job["id"]))
        thread = threading.Thread(target=work)
        self.jobs.append(thread)
        thread.start()
        return thread, outputs

    def test_settings_do_not_put_secret_in_database_or_public_response(self) -> None:
        self.settings()
        with server.connect() as conn:
            public = moyu_settings.public_settings(conn, self.project)
            saved = conn.execute("SELECT settings_json FROM app_model_settings").fetchone()[0]
            private = moyu_settings.provider_settings(conn, self.project)
        self.assertNotIn("fixture-original-credential", json.dumps(public))
        self.assertNotIn("fixture-original-credential", saved)
        self.assertEqual(private["api_key"], "fixture-original-credential")
        files = list((self.root / "private-keys").glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(files[0].parent.stat().st_mode), 0o700)
        self.assertEqual(public["parameters"]["temperature"], 0)

    def test_completed_request_audit_and_complete_backup_never_contain_credential(self) -> None:
        self.settings()
        job = self.enqueue()
        completed = server.process_ai_job(job["id"])
        with server.connect() as conn:
            public = server.ai_job_public(
                conn.execute("SELECT * FROM app_ai_job WHERE id=?", (job["id"],)).fetchone())
            backup = moyu_backups.capture(conn, self.project)
            database_dump = "\n".join(conn.iterdump())
        for payload in (completed, public, backup):
            self.assertNotIn("fixture-original-credential", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("fixture-original-credential", database_dump)
        self.assertNotIn('"api_key"', json.dumps(backup))
        self.assertTrue(completed["result"]["request_audit"]["dispatched"])

    def test_credential_in_usage_keys_cannot_reach_public_ledger_or_backup(self) -> None:
        self.settings("usage-secret-key")
        job = self.enqueue()
        completed = server.process_ai_job(job["id"])
        with server.connect() as conn:
            public = server.ai_job_public(
                conn.execute("SELECT * FROM app_ai_job WHERE id=?", (job["id"],)).fetchone(), conn=conn)
            backup = moyu_backups.capture(conn, self.project)
            token_json = conn.execute("SELECT token_json FROM ai_run WHERE id=?", (job["ai_run_id"],)).fetchone()[0]
            database_dump = "\n".join(conn.iterdump())
        self.assertEqual(completed["status"], "success")
        self.assertEqual(completed["result"]["usage"]["total_tokens"], 20)
        for payload in (completed, public, backup):
            self.assertNotIn("fixture-original-credential", json.dumps(payload, ensure_ascii=False))
        for text in (token_json, database_dump):
            self.assertNotIn("fixture-original-credential", text)

    def test_job_refresh_reads_persisted_author_decision_after_accept_edit_and_undo(self) -> None:
        self.settings()
        job = self.enqueue()
        server.process_ai_job(job["id"])
        for action, expected in (("accepted", "accepted"), ("edited", "edited"), ("undone", "rejected")):
            with server.connect() as conn:
                server.record_ai_decision(conn, job["ai_run_id"], action)
            with server.connect() as conn:
                row = conn.execute("SELECT * FROM app_ai_job WHERE id=?", (job["id"],)).fetchone()
                public = server.ai_job_public(row, conn=conn)
                self.assertEqual(public["author_decision"], expected)
                self.assertEqual(public["result"]["author_decision"], expected)
                self.assertEqual(public["result"]["latest_decision"], action)
                self.assertNotIn("consent_json", public)
                self.assertNotIn("context_json", public)

    def test_database_rollback_preserves_previous_credential(self) -> None:
        self.settings()
        conn = server.connect()
        try:
            moyu_settings.save_settings(conn, self.project, {"api_key": "fixture-new-credential"})
            conn.rollback()
            self.assertEqual(moyu_settings.provider_settings(conn, self.project)["api_key"],
                             "fixture-original-credential")
        finally:
            conn.close()

    def test_credential_file_permission_must_be_private(self) -> None:
        self.settings()
        path = next((self.root / "private-keys").glob("*.json"))
        path.chmod(0o644)
        with server.connect() as conn, self.assertRaises(ValueError):
            moyu_settings.provider_settings(conn, self.project)
        path.chmod(0o600)

    def test_model_change_invalidates_old_probe(self) -> None:
        self.settings("probe")
        with server.connect() as conn:
            probe = moyu_settings.probe_settings(conn, self.project)
            self.assertTrue(probe["probe"]["success"])
            self.assertTrue(moyu_settings.public_settings(conn, self.project)["last_probe"]["success"])
            changed = moyu_settings.save_settings(conn, self.project, {"model": "new-model"})
            self.assertIsNone(changed["last_probe"])

    def test_failed_probe_records_safe_error_and_unknown_charge(self) -> None:
        self.settings("unauthorized", limit=1)
        with server.connect() as conn:
            probe = moyu_settings.probe_settings(conn, self.project)
            self.assertFalse(probe["probe"]["success"])
            self.assertEqual(probe["probe"]["error_code"], "provider_http_error")
            self.assertEqual(probe["probe"]["billing_state"], "unknown")
            self.assertNotIn("fixture-original-credential", json.dumps(probe))
            self.assertEqual(server.ai_quota_snapshot(conn)["remaining_runs"], 0)
            public = moyu_settings.public_settings(conn, self.project)
            self.assertFalse(public["last_probe"]["success"])

    def test_configuration_change_during_probe_cannot_mark_new_settings_healthy(self) -> None:
        self.settings("slow")
        before = len(Handler.calls)
        outcomes = []
        def work() -> None:
            with server.connect() as conn:
                outcomes.append(moyu_settings.probe_settings(conn, self.project))
        thread = threading.Thread(target=work)
        self.jobs.append(thread)
        thread.start()
        for _ in range(100):
            if len(Handler.calls) > before:
                break
            time.sleep(0.01)
        self.assertGreater(len(Handler.calls), before)
        self.settings("probe", model="replacement-model")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(outcomes[0]["probe"]["configuration_stale"])
        with server.connect() as conn:
            public = moyu_settings.public_settings(conn, self.project)
            self.assertEqual(public["model"], "replacement-model")
            self.assertIsNone(public["last_probe"])

    def test_explicit_clear_remains_cleared_after_probe_with_environment_key(self) -> None:
        self.settings("probe")
        os.environ["MOYU_AI_API_KEY"] = "fixture-environment-credential"
        try:
            with server.connect() as conn:
                moyu_settings.save_settings(conn, self.project, {"clear_api_key": True})
                probe = moyu_settings.probe_settings(conn, self.project)
                self.assertTrue(probe["probe"]["success"])
                self.assertFalse(probe["settings"]["api_key_configured"])
                self.assertEqual(moyu_settings.provider_settings(conn, self.project)["api_key"], "")
        finally:
            os.environ["MOYU_AI_API_KEY"] = ""

    def test_environment_credential_change_refuses_frozen_job(self) -> None:
        previous_base = os.environ["MOYU_AI_BASE_URL"]
        previous_model = os.environ["MOYU_AI_MODEL"]
        os.environ["MOYU_AI_BASE_URL"] = self.base + "/normal"
        os.environ["MOYU_AI_MODEL"] = "environment-model"
        os.environ["MOYU_AI_API_KEY"] = "fixture-environment-original"
        try:
            with server.connect() as conn:
                config = moyu_settings.provider_settings(conn, self.project)
                snapshot = {key: value for key, value in config.items() if key != "api_key"}
                os.environ["MOYU_AI_API_KEY"] = "fixture-environment-changed"
                with self.assertRaises(ValueError):
                    moyu_settings.frozen_provider_settings(conn, self.project, snapshot)
        finally:
            os.environ["MOYU_AI_BASE_URL"] = previous_base
            os.environ["MOYU_AI_MODEL"] = previous_model
            os.environ["MOYU_AI_API_KEY"] = ""

    def test_missing_frozen_credential_fails_before_dispatch_and_releases_quota(self) -> None:
        self.settings(limit=1)
        job = self.enqueue()
        path = next((self.root / "private-keys").glob("*.json"))
        path.unlink()
        before = len(Handler.calls)
        completed = server.process_ai_job(job["id"])
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["result"]["billing_state"], "not_dispatched")
        self.assertEqual(completed["result"]["error_code"], "dispatch_precondition_failed")
        self.assertEqual(len(Handler.calls), before)
        with server.connect() as conn:
            self.assertEqual(server.ai_quota_snapshot(conn)["remaining_runs"], 1)
            self.assertEqual(conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                         (job["ai_run_id"],)).fetchone()[0], "released")

    def test_probe_consumes_the_same_atomic_daily_quota(self) -> None:
        self.settings("probe", limit=1)
        before = len(Handler.calls)
        with server.connect() as conn:
            first = moyu_settings.probe_settings(conn, self.project)
            self.assertTrue(first["probe"]["success"])
        with server.connect() as conn:
            self.assertEqual(server.ai_quota_snapshot(conn)["remaining_runs"], 0)
            with self.assertRaises(ValueError):
                moyu_settings.probe_settings(conn, self.project)
        self.assertEqual(len(Handler.calls), before + 1)

    def test_settings_are_project_specific(self) -> None:
        self.settings()
        with server.connect() as conn:
            conn.execute("INSERT INTO project(id,schema_version,name,created_at) VALUES(?,?,?,?)",
                         ("other-project", 11, "另一部书", server.now()))
            moyu_settings.save_settings(conn, "other-project",
                                       {"base_url": self.base + "/normal", "model": "other-model",
                                        "api_key": "fixture-other-credential", "parameters": {"temperature": 1}})
            self.assertEqual(moyu_settings.provider_settings(conn, self.project)["api_key"], "fixture-original-credential")
            self.assertEqual(moyu_settings.provider_settings(conn, "other-project")["api_key"], "fixture-other-credential")

    def test_job_freezes_prompt_parameters_model_credential_and_source(self) -> None:
        self.settings(model="original-model", temperature=0)
        job = self.enqueue()
        self.settings(model="new-model", temperature=1.7, key="fixture-new-credential")
        completed = server.process_ai_job(job["id"])
        sent = Handler.calls[-1]
        self.assertEqual(sent["payload"]["model"], "original-model")
        self.assertEqual(sent["payload"]["temperature"], 0)
        self.assertEqual(sent["authorization"], "Bearer fixture-original-credential")
        self.assertEqual(completed["result"]["source"]["revision_id"], "fixture-source-revision")
        with server.connect() as conn:
            run = conn.execute("SELECT sampling_json,token_json,consent_json FROM ai_run WHERE id=?", (job["ai_run_id"],)).fetchone()
        self.assertEqual(json.loads(run["sampling_json"])["temperature"], 0)
        self.assertEqual(json.loads(run["token_json"])["usage"]["total_tokens"], 20)
        self.assertNotIn("fixture-original-credential", "".join(run))
        self.assertEqual(completed["result"]["text"], "先到正文后到正文")

    def test_parallel_submission_cannot_over_reserve_quota_one(self) -> None:
        self.settings(limit=1)
        barrier = threading.Barrier(2)
        outcomes = []
        def reserve() -> None:
            barrier.wait()
            try:
                outcomes.append(self.enqueue())
            except ValueError:
                outcomes.append("quota-rejected")
        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        self.assertEqual(outcomes.count("quota-rejected"), 1)
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_job").fetchone()[0], 1)
            self.assertEqual(server.ai_quota_snapshot(conn)["used_runs"], 1)

    def test_chunks_persist_and_consumed_material_changes_save_without_blocking(self) -> None:
        self.settings("slow")
        job = self.enqueue()
        thread, outputs = self.process_thread(job)
        self.wait_chunks(job)
        self.assertTrue(thread.is_alive())
        started = time.monotonic()
        with server.connect() as conn:
            conn.execute("UPDATE app_project_meta SET summary='正常保存' WHERE project_id=?", (self.project,))
        self.assertLess(time.monotonic() - started, 0.3)
        thread.join(timeout=3)
        completed = outputs[0]
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["result"]["completion_state"], "invalid_evidence")
        self.assertEqual(completed["result"]["text"], "先到正文后到正文")
        self.assertFalse(completed["result"]["safe_to_apply"])
        self.assertEqual(completed["result"]["billing_state"], "usage_reported")
        with server.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                (job["ai_run_id"],)).fetchone()[0], "consumed")
            self.assertEqual(conn.execute(
                "SELECT summary FROM app_project_meta WHERE project_id=?",
                (self.project,)).fetchone()[0], "正常保存")

    def test_running_cancel_stops_transport_and_retains_partial_ledger(self) -> None:
        self.settings("slow", limit=1)
        job = self.enqueue()
        thread, outputs = self.process_thread(job)
        self.wait_chunks(job)
        with server.connect() as conn:
            conn.execute("UPDATE app_ai_job SET cancel_requested=1 WHERE id=?", (job["id"],))
        started = time.monotonic()
        server.cancel_ai_job(job["id"])
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(outputs[0]["status"], "canceled")
        self.assertFalse(outputs[0]["result"]["safe_to_apply"])
        self.assertEqual(outputs[0]["result"]["billing_state"], "unknown")
        with server.connect() as conn:
            self.assertEqual(server.ai_quota_snapshot(conn)["remaining_runs"], 0)
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM app_ai_chunk WHERE ai_run_id=?", (job["ai_run_id"],)).fetchone()[0], 0)

    def test_queued_cancel_releases_reserved_budget(self) -> None:
        self.settings(limit=1)
        job = self.enqueue()
        with server.connect() as conn:
            conn.execute("UPDATE app_ai_job SET cancel_requested=1 WHERE id=?", (job["id"],))
        completed = server.process_ai_job(job["id"])
        self.assertEqual(completed["status"], "canceled")
        self.assertEqual(completed["result"]["billing_state"], "not_dispatched")
        with server.connect() as conn:
            self.assertEqual(server.ai_quota_snapshot(conn)["remaining_runs"], 1)

    def test_eof_job_is_unknown_not_success_and_never_recovered_as_queued(self) -> None:
        self.settings("eof", limit=1)
        job = self.enqueue()
        completed = server.process_ai_job(job["id"])
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["result"]["completion_state"], "unknown")
        self.assertFalse(completed["result"]["safe_to_apply"])
        self.assertEqual(server.recover_ai_jobs(), [])
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM ai_run WHERE id=?", (job["ai_run_id"],)).fetchone()[0], "unknown")
            self.assertEqual(server.ai_quota_snapshot(conn)["remaining_runs"], 0)

    def test_wrong_structured_output_is_failed_with_safe_partial(self) -> None:
        self.settings("schema")
        job = self.enqueue("scene", "json")
        completed = server.process_ai_job(job["id"])
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["result"]["error_code"], "invalid_structured_output")
        self.assertFalse(completed["result"]["safe_to_apply"])
        self.assertIn('"pov"', completed["result"]["text"])

    def test_final_chunk_persistence_is_idempotent_with_exact_cumulative_hashes(self) -> None:
        self.settings()
        job = self.enqueue()
        server.process_ai_job(job["id"])
        with server.connect() as conn:
            rows = conn.execute("SELECT seq,text,payload_hash,cumulative_hash FROM app_ai_chunk WHERE ai_run_id=? ORDER BY seq",
                                (job["ai_run_id"],)).fetchall()
            chunks = [dict(row) for row in rows]
            self.assertEqual([row["seq"] for row in rows], [1, 2])
            self.assertEqual(rows[1]["cumulative_hash"], server.hash_text("先到正文后到正文"))
            summary = server.persist_ai_chunks(conn, job["ai_run_id"], "先到正文后到正文", chunks)
            self.assertEqual(summary["chunk_count"], 2)
            self.assertEqual(summary["output_hash"], server.hash_text("先到正文后到正文"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_chunk WHERE ai_run_id=?", (job["ai_run_id"],)).fetchone()[0], 2)
            changed = {**chunks[1], "cumulative_hash": server.hash_text("后到正文")}
            with self.assertRaises(ValueError):
                server.persist_ai_chunks(conn, job["ai_run_id"], "后到正文", [changed])

    def test_incremental_local_chunk_hash_covers_preceding_chunks_and_rejects_gaps(self) -> None:
        self.settings()
        job = self.enqueue()
        with server.connect() as conn:
            server.persist_ai_chunks(conn, job["ai_run_id"], "先到正文", [{"seq": 1, "text": "先到正文"}])
            summary = server.persist_ai_chunks(conn, job["ai_run_id"], "后到正文", [{"seq": 2, "text": "后到正文"}])
            self.assertEqual(summary["output_hash"], server.hash_text("先到正文后到正文"))
            saved = conn.execute("SELECT cumulative_hash FROM app_ai_chunk WHERE ai_run_id=? AND seq=2",
                                 (job["ai_run_id"],)).fetchone()[0]
            self.assertEqual(saved, summary["output_hash"])
            with self.assertRaises(ValueError):
                server.persist_ai_chunks(conn, job["ai_run_id"], "不应出现", [{"seq": 4, "text": "不应出现"}])

    def test_crash_recovery_keeps_unknown_charge_without_dispatch(self) -> None:
        self.settings(limit=1)
        job = self.enqueue()
        before = len(Handler.calls)
        with server.connect() as conn:
            conn.execute("UPDATE app_ai_job SET status='running' WHERE id=?", (job["id"],))
            conn.execute("UPDATE ai_run SET status='running' WHERE id=?", (job["ai_run_id"],))
        self.assertEqual(server.recover_ai_jobs(), [])
        self.assertEqual(len(Handler.calls), before)
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM ai_run WHERE id=?", (job["ai_run_id"],)).fetchone()[0], "unknown")
            self.assertEqual(server.ai_quota_snapshot(conn)["used_runs"], 1)

    def test_provider_concurrency_is_bounded_by_two(self) -> None:
        while Handler.active:
            time.sleep(0.01)
        Handler.max_active = 0
        self.settings()
        jobs = [self.enqueue() for _ in range(5)]
        for job in jobs:
            server.start_ai_worker(job["id"])
        for _ in range(200):
            with server.connect() as conn:
                pending = conn.execute("SELECT COUNT(*) FROM app_ai_job WHERE status IN ('queued','running')").fetchone()[0]
            if not pending:
                break
            time.sleep(0.01)
        self.assertEqual(pending, 0)
        self.assertLessEqual(Handler.max_active, 2)
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_job WHERE status='success'").fetchone()[0], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
