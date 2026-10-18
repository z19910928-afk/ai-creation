"""Dual-protocol author workflows through isolated SQLite and loopback HTTP."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_backups
import moyu_materials
import moyu_revision
from moyu_provider import prepare_chat_request
import server


SECRET = "synthetic-responses-workflow-credential"
PROSE = "林岚将铜钥匙放在门槛上，退后一步，等着门内的回应。"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


class Provider(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.calls.append({"method": "GET", "path": self.path})
        self.reply({"data": [{"id": "workflow-chat"}, {"id": "workflow-responses"}]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append({"method": "POST", "path": self.path, "payload": payload,
                                  "key_matches": self.headers.get("Authorization") == "Bearer " + SECRET})
        messages = payload.get("input", payload.get("messages", []))
        probe = any(message["content"] == "连接测试。只回复：连接正常" for message in messages)
        is_chat = any("你是墨屿的写作总管" in message["content"] for message in messages)
        text = "连接正常" if probe else (
            json.dumps({"reply": "林岚仍在门外，守卫是否知道钥匙来历尚未确定。", "actions": []},
                       ensure_ascii=False) if is_chat else PROSE)
        model = payload["model"]
        if self.path.endswith("/chat/completions"):
            self.reply({"model": model, "choices": [
                {"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})
            return
        if not self.path.endswith("/responses"):
            self.send_error(404)
            return
        output = [{"type": "message", "id": "msg_fixture", "role": "assistant",
                   "status": "completed", "content": [
                       {"type": "output_text", "text": text, "annotations": []}]}]
        response = {"id": "resp_fixture", "object": "response", "status": "completed",
                    "model": model, "output": output,
                    "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}}
        if not payload.get("stream"):
            self.reply(response)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        created = {**response, "status": "in_progress", "output": [], "usage": None}
        self.event("response.created", {"response": created})
        for index, part in enumerate((text[:8], text[8:])):
            self.event("response.output_text.delta", {
                "item_id": "msg_fixture", "output_index": 0, "content_index": 0,
                "delta": part, "sequence_number": index + 1})
        self.event("response.output_text.done", {
            "item_id": "msg_fixture", "output_index": 0, "content_index": 0, "text": text})
        self.event("response.completed", {"response": response})

    def event(self, name, value):
        self.wfile.write(("event: " + name + "\ndata: ").encode()
                         + encoded({"type": name, **value}) + b"\n\n")
        self.wfile.flush()

    def reply(self, value):
        body = encoded(value)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class QuietApp(server.Handler):
    def log_message(self, *_args):
        pass


class ResponsesWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix="moyu-responses-workflow-")))
        self.stack.enter_context(patch.dict(os.environ, {
            "MOYU_CREDENTIALS_DIR": str(self.root / "credentials"),
            "MOYU_AI_BASE_URL": "", "MOYU_AI_API_KEY": "", "MOYU_AI_MODEL": "",
            "MOYU_AI_DAILY_QUOTA": "100",
        }))
        self.stack.enter_context(patch.object(server, "DB_PATH", self.root / "book.sqlite3"))
        self.stack.enter_context(patch.object(server, "start_ai_worker", return_value=False))
        server.init_db()
        self.project = server.PROJECT_ID
        with server.connect() as conn:
            self.chapter = server.chapter_rows(conn)[0]["id"]
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.provider.calls = []
        self.app = ThreadingHTTPServer(("127.0.0.1", 0), QuietApp)
        self.threads = []
        for service in (self.provider, self.app):
            service.daemon_threads = True
            thread = threading.Thread(target=service.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.provider_base = f"http://127.0.0.1:{self.provider.server_port}/v1"

    def tearDown(self):
        for service in (self.app, self.provider):
            service.shutdown()
            service.server_close()
        for thread in self.threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    def request(self, path, method="GET", body=None, expected=200):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.app.server_port}" + path, method=method,
            data=None if body is None else encoded(body),
            headers={"Content-Type": "application/json", "X-Moyu-Project": self.project})
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            value = json.load(response)
            self.assertEqual(response.status, expected, value)
        self.assertNotIn(SECRET, json.dumps(value))
        return value

    def register(self, protocol="responses"):
        probe = self.request("/api/ai/connections/probe", "POST", {
            "name": protocol, "base_url": self.provider_base,
            "api_key": SECRET, "model": "workflow-" + (
                "responses" if protocol == "responses" else "chat"),
            "api_protocol": protocol,
        })
        self.assertTrue(probe["probe"]["success"], probe)
        return self.request("/api/ai/connections", "POST", {
            "name": protocol, "verification_token": probe["verification_token"]})["connection"]

    def assign(self, connection, **values):
        return self.request("/api/ai/settings", "PUT", {
            "default_connection_id": connection["id"],
            "parameters": {"temperature": 0.2, "max_tokens": 600}, **values})

    def queue(self):
        return self.request("/api/ai/queue", "POST", {
            "task": "continue", "chapter_id": self.chapter, "defer": True}, expected=202)

    def test_discovery_does_not_write_keys_or_settings_or_reserve_generation(self):
        for protocol in ("chat_completions", "responses"):
            result = self.request("/api/ai/connections/models", "POST", {
                "base_url": self.provider_base + "/responses", "api_key": SECRET,
                "api_protocol": protocol})
            self.assertEqual(result["models"], ["workflow-chat", "workflow-responses"])
        self.assertEqual([item["path"] for item in self.provider.calls], ["/v1/models"] * 2)
        with server.connect() as conn:
            for table in ("app_model_settings", "app_quota_reservation", "ai_run", "app_ai_job"):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0)
        self.assertFalse(list((self.root / "credentials").glob("*.json")))

    def test_response_queue_keeps_exact_request_after_default_changes(self):
        response_connection, chat_connection = self.register(), self.register("chat_completions")
        self.assign(response_connection, task_connections={"polish": chat_connection["id"]})
        before = len(self.provider.calls)
        queued = self.queue()
        self.assertEqual(len(self.provider.calls), before)
        with server.connect() as conn:
            consent = json.loads(conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                             (queued["ai_run_id"],)).fetchone()[0])
        payload = consent["request_audit"]["payload"]
        self.assertEqual(consent["provider_config"]["api_protocol"], "responses")
        self.assertEqual(payload["max_output_tokens"], 600)
        self.assertFalse(payload["store"])
        self.assertNotIn("messages", payload)
        self.assign(chat_connection)
        self.assertIn(queued["id"], server.recover_ai_jobs())
        completed = self.request("/api/ai/jobs/" + queued["id"] + "/process", "POST", {})
        self.assertEqual(completed["status"], "success", completed)
        actual = self.provider.calls[-1]
        self.assertEqual(actual["path"], "/v1/responses")
        self.assertEqual(actual["payload"], payload)
        self.assertTrue(actual["key_matches"])
        self.assertEqual(completed["result"]["text"], PROSE)
        self.assertTrue(completed["result"]["safe_to_apply"])
        self.assertEqual(completed["result"]["usage"]["total_tokens"], 30)

    def test_changed_frozen_response_input_is_rejected_before_provider_dispatch(self):
        self.assign(self.register())
        queued = self.queue()
        with server.connect() as conn:
            row = conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                               (queued["ai_run_id"],)).fetchone()
            consent = json.loads(row[0])
            consent["request_audit"]["payload"]["input"][-1]["content"] += "changed"
            conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?",
                         (json.dumps(consent, ensure_ascii=False), queued["ai_run_id"]))
        before = len(self.provider.calls)
        completed = self.request("/api/ai/jobs/" + queued["id"] + "/process", "POST", {})
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(len(self.provider.calls), before)

    def test_candidate_revision_preserves_response_protocol_and_original_messages(self):
        self.assign(self.register())
        queued = self.queue()
        original = self.request("/api/ai/jobs/" + queued["id"] + "/process", "POST", {})
        self.assertEqual(original["status"], "success", original)
        original_payload = copy.deepcopy(self.provider.calls[-1]["payload"])
        before = len(self.provider.calls)
        revised = self.request("/api/ai/runs/" + queued["ai_run_id"] + "/revise", "POST", {
            "request_id": "response_revision", "candidate_hash": server.hash_text(PROSE),
            "notes": "动作更简洁。", "defer": True}, expected=202)["job"]
        self.assertEqual(len(self.provider.calls), before)
        completed = self.request("/api/ai/jobs/" + revised["id"] + "/process", "POST", {})
        self.assertEqual(completed["status"], "success", completed)
        actual = self.provider.calls[-1]
        self.assertEqual(actual["path"], "/v1/responses")
        self.assertEqual(actual["payload"]["input"][1], original_payload["input"][1])
        self.assertIn("候选整理要求", actual["payload"]["input"][0]["content"])
        self.assertEqual(json.loads(actual["payload"]["input"][-1]["content"])["candidate_text"], PROSE)
        self.assertEqual(actual["payload"]["max_output_tokens"], original_payload["max_output_tokens"])
        self.assertNotIn("messages", actual["payload"])

    def test_inherited_and_independent_agent_chat_use_response_protocol(self):
        self.assign(self.register())
        for independent in (False, True):
            with self.subTest(independent=independent):
                if independent:
                    saved = self.request("/api/agent-chat/settings", "POST", {
                        "mode": "independent", "base_url": self.provider_base,
                        "api_key": SECRET, "model": "workflow-responses",
                        "api_protocol": "responses",
                        "parameters": {"temperature": 0.3, "max_tokens": 500}})
                    self.assertEqual(saved["settings"]["api_protocol"], "responses")
                    before = len(self.provider.calls)
                    listed = self.request("/api/agent-chat/settings/models", "POST", {
                        "base_url": self.provider_base, "api_protocol": "responses"})
                    self.assertEqual(len(self.provider.calls), before + 1)
                    self.assertEqual(listed["models"], ["workflow-chat", "workflow-responses"])
                session = self.request("/api/agent-chat/sessions", "POST", {
                    "request_id": "session_" + str(independent)}, expected=201)["session"]
                queued = self.request("/api/agent-chat/sessions/" + session["id"] + "/messages",
                                      "POST", {"request_id": "turn_" + str(independent),
                                               "content": "钥匙放在哪里？",
                                               "chapter_id": self.chapter}, expected=202)
                completed = server.process_ai_job(queued["active_run"]["id"])
                self.assertEqual(completed["status"], "success", completed)
                actual = self.provider.calls[-1]
                self.assertEqual(actual["path"], "/v1/responses")
                self.assertEqual(actual["payload"]["text"], {"format": {"type": "json_object"}})
                read = self.request("/api/agent-chat/sessions/" + session["id"])
                self.assertIn("林岚仍在门外", json.dumps(read, ensure_ascii=False))

    def test_revision_and_reference_hashes_keep_response_input_bound(self):
        context = {"reference_passages": [{"section_id": "fixture-reference", "quote": "门槛"}]}
        messages = [{"role": "system", "content": "保留已确认事实。"},
                    {"role": "user", "content": "<manuscript>\n" + encoded(context).decode()
                     + "\n</manuscript>\n作者要求：保留位置。"}]
        prepared = prepare_chat_request(
            {"base_url": self.provider_base, "model": "workflow-responses",
             "api_protocol": "responses"}, messages, max_tokens=600, stream=True)
        audit = prepared["request_audit"]
        moyu_materials.validate_frozen_reference_context(
            context, audit, input_hash=hashlib.sha256(encoded(context)).hexdigest(),
            prompt_hash=audit["request_sha256"], require_full_request=True)
        changed = copy.deepcopy(audit)
        changed["payload"]["input"][1]["content"] += " changed"
        changed["request_sha256"] = hashlib.sha256(encoded(changed["payload"])).hexdigest()
        with self.assertRaises(ValueError):
            moyu_materials.validate_frozen_reference_context(context, changed, require_full_request=True)
        revised = moyu_revision.build_revision_request(
            prepared["payload"], "continue", PROSE,
            {"minimum_characters": 1, "maximum_characters": 2000},
            "减少重复。", api_protocol="responses")
        self.assertEqual(prepared["payload"]["input"], messages)
        self.assertEqual(len(revised["input"]), 3)
        self.assertIn("候选整理要求", revised["input"][0]["content"])
        self.assertEqual(revised["max_output_tokens"], 600)
        self.assertNotIn("messages", revised)

    def test_portable_model_settings_keep_protocol_but_require_new_verification(self):
        raw = {
            "api_protocol": "responses", "base_url": self.provider_base,
            "model": "workflow-responses", "_credential_ref": SECRET,
            "last_probe": {"success": True}, "default_connection_id": "fixture",
            "task_connections": {"polish": "fixture"},
            "connections": [{"id": "fixture", "name": "测试", "base_url": self.provider_base,
                             "model": "workflow-responses", "api_protocol": "responses",
                             "output_token_parameter": "max_completion_tokens", "include_usage": True,
                             "_credential_ref": SECRET, "last_probe": {"success": True}}],
        }
        restored = json.loads(moyu_backups._portable_model_settings(json.dumps(raw)))
        self.assertEqual(restored["api_protocol"], "responses")
        self.assertEqual(restored["connections"][0]["api_protocol"], "responses")
        self.assertEqual(restored["connections"][0]["output_token_parameter"], "max_completion_tokens")
        self.assertTrue(restored["connections"][0]["include_usage"])
        self.assertIsNone(restored["connections"][0]["last_probe"])
        self.assertIsNone(restored["connections"][0]["_credential_ref"])
        self.assertEqual(restored["default_connection_id"], "")
        self.assertEqual(restored["task_connections"], {})
        self.assertNotIn(SECRET, json.dumps(restored))
        raw["connections"][0]["api_protocol"] = "unknown"
        with self.assertRaises(ValueError):
            moyu_backups._portable_model_settings(json.dumps(raw))


if __name__ == "__main__":
    unittest.main()
