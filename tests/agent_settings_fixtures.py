"""Agent draft verification and library routing using isolated local providers."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_agent_settings as agent
from moyu_agent_chat import SCHEMA as AGENT_CHAT_SCHEMA
import moyu_model_connections as library
import moyu_settings
import moyu_workspace
from moyu_provider import call_chat_completion

MAIN_KEY = "synthetic-main-connection-private-key"
AGENT_KEY = "synthetic-agent-connection-private-key"


class Provider(BaseHTTPRequestHandler):
    calls: list[dict] = []
    model_gets: list[dict] = []
    database: Path | None = None
    started = threading.Event()
    release = threading.Event()

    def do_GET(self):
        if not self.path.endswith("/models"):
            return self.send_error(405)
        self.model_gets.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                "user_agent": self.headers.get("User-Agent")})
        self.reply({"data": [{"id": "agent-fixture"}, {"id": "other-fixture"}, {"id": "agent-fixture"}]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        case = self.path.split("/")[1]
        with sqlite3.connect(self.database) as conn:
            ledger = conn.execute(
                "SELECT q.status,a.consent_json FROM ai_run a JOIN app_quota_reservation q "
                "ON q.ai_run_id=a.id WHERE a.id=?", (self.headers.get("X-Client-Request-Id"),),
            ).fetchone()
        self.calls.append({"path": self.path, "payload": payload,
                           "authorization": self.headers.get("Authorization"),
                           "user_agent": self.headers.get("User-Agent"),
                           "ledger": (ledger[0], json.loads(ledger[1])) if ledger else None})
        if case == "held":
            self.started.set()
            self.release.wait(timeout=5)
        message = {"content": "连接正常。" if case == "punctuation" else
                   "连接正常，还可以写小说" if case == "extra" else "连接正常"}
        if (payload.get("text", {}).get("format", {}).get("type") == "json_object"
                or payload.get("response_format", {}).get("type") == "json_object"):
            message["content"] = json.dumps({"reply": "已读取已确认的设定。", "actions": []}, ensure_ascii=False)
        if case == "reasoning":
            message["reasoning"] = "fixture-private-reasoning"
        if case == "invalid-reasoning":
            message["reasoning_content"] = {}
        body = {
            "model": "different-fixture" if case == "mismatch" else payload["model"],
            "choices": [{"message": message, "finish_reason": "length" if case == "truncated" else "stop"}],
            "usage": {"completion_tokens": 7} if case == "partial-usage" else
                     {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20},
        }
        if case == "unreported":
            body.pop("model")
        if self.path.endswith("/responses"):
            body = {
                **({"model": body["model"]} if "model" in body else {}),
                "id": "resp-agent-fixture", "object": "response", "status": "completed",
                "output": [{"id": "msg-agent-fixture", "type": "message", "role": "assistant",
                            "status": "completed", "content": [
                                {"type": "output_text", "text": message["content"], "annotations": []}]}],
                "usage": {"input_tokens": 13, "output_tokens": 7, "total_tokens": 20},
            }
        self.reply(body, 401 if case == "unauthorized" else 200)

    def reply(self, body, status=200):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


class AgentSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        cls.provider.daemon_threads = True
        cls.thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.provider.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="moyu-agent-settings-fixture-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "book.sqlite3"
        self.environment = patch.dict(os.environ, {
            "MOYU_CREDENTIALS_DIR": str(self.root / "credentials"),
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_AI_DAILY_QUOTA": "100", "MOYU_AI_TEMPERATURE": "0.65",
        })
        self.environment.start()
        self.project = "agent-fixture"
        Provider.database = self.database
        Provider.calls.clear()
        Provider.model_gets.clear()
        Provider.started.clear()
        Provider.release.clear()
        self.probe_threads = []
        with self.connect() as conn:
            conn.executescript((ROOT / "docs/schema-v1.sql").read_text(encoding="utf-8"))
            for column in ("context_item_ids_json TEXT", "token_json TEXT", "cost_json TEXT",
                           "author_decision TEXT", "consent_json TEXT", "latency_ms INTEGER"):
                conn.execute("ALTER TABLE ai_run ADD COLUMN " + column)
            conn.execute("CREATE TABLE app_state_version(project_id TEXT PRIMARY KEY,version INTEGER NOT NULL DEFAULT 0)")
            moyu_workspace.install_schema(conn)
            agent.install_schema(conn)
            agent.install_schema(conn)
            conn.execute("INSERT INTO project VALUES(?,1,'fixture','2026-10-18')", (self.project,))
            conn.execute("INSERT INTO branch(id,project_id,status) VALUES('agent-main',?,'active')", (self.project,))
            conn.execute("INSERT INTO app_state_version(project_id) VALUES(?)", (self.project,))

    def tearDown(self):
        Provider.release.set()
        for thread in self.probe_threads:
            thread.join(timeout=5)
        self.environment.stop()
        self.temporary.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.database, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def draft(self, case="normal", **overrides):
        return {"mode": "independent", "base_url": self.base + "/" + case,
                "model": "agent-fixture", "api_key": AGENT_KEY,
                "parameters": {"temperature": 0, "max_tokens": 3000, "thinking": {"type": "disabled"}},
                **overrides}

    def probe_draft(self, case="normal", **overrides):
        with self.connect() as conn:
            return agent.probe_draft(conn, self.project, self.draft(case, **overrides))

    def save_verified(self, case="normal", **overrides):
        result = self.probe_draft(case, **overrides)
        self.assertTrue(result["probe"]["success"], result)
        with self.connect() as conn:
            return agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})

    def save_main(self, **overrides):
        with self.connect() as conn:
            return moyu_settings.save_settings(conn, self.project, {
                "base_url": self.base + "/main", "model": "main-fixture",
                "api_key": MAIN_KEY, "daily_limit": 100, **overrides})

    def raw(self):
        with self.connect() as conn:
            return agent._saved(conn, self.project)

    def active(self):
        with self.connect() as conn:
            return agent.provider_settings(conn, self.project)

    def library_model(self, model="library-fixture", **overrides):
        with self.connect() as conn:
            check = library.probe_connection(conn, self.project, {
                "base_url": self.base + "/normal", "model": model,
                "name": model, "api_key": MAIN_KEY, **overrides})
        self.assertTrue(check["probe"]["success"], check)
        with self.connect() as conn:
            return library.save_connection(conn, self.project,
                                           {"verification_token": check["verification_token"]})["connection"]

    def test_inherited_main_keeps_credentials_private(self):
        self.save_main(parameters={"temperature": 0, "max_tokens": 1200})
        with self.connect() as conn:
            public = agent.public_settings(conn, self.project)
        self.assertEqual(public["mode"], "inherit")
        self.assertTrue(public["configured"])
        self.assertEqual(self.active()["api_key"], MAIN_KEY)
        self.assertEqual(public["parameters"], {"temperature": 0, "max_tokens": 1200})
        self.assertNotIn(MAIN_KEY, json.dumps(public))
        self.assertNotIn("credential_ref", json.dumps(public))

    def test_unverified_independent_save_is_rejected_without_mutation(self):
        before = self.raw()
        with self.connect() as conn, self.assertRaisesRegex(ValueError, "先测试"):
            agent.save_settings(conn, self.project, self.draft())
        self.assertEqual(self.raw(), before)
        self.assertEqual(Provider.calls, [])

    def test_successful_draft_does_not_activate_until_receipt_saved(self):
        self.save_main()
        before = self.active()
        result = self.probe_draft()
        self.assertTrue(result["probe"]["success"])
        self.assertIn("verification_token", result)
        self.assertEqual(self.active(), before)
        self.assertEqual(result["settings"]["mode"], "inherit")
        self.assertNotIn(AGENT_KEY, json.dumps(result))
        with self.connect() as conn:
            public = agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})
        self.assertTrue(public["configured"])
        self.assertTrue(public["last_probe"]["success"])
        self.assertEqual(self.active()["api_key"], AGENT_KEY)
        self.assertEqual(len(Provider.calls), 1)

    def test_failed_draft_preserves_previous_verified_connection(self):
        self.save_verified()
        before = self.raw()
        config = self.active()
        result = self.probe_draft("mismatch", model="replacement-fixture")
        self.assertFalse(result["probe"]["success"])
        self.assertNotIn("verification_token", result)
        self.assertEqual(self.raw(), before)
        self.assertEqual(self.active(), config)

    def test_receipt_is_single_use_and_not_a_public_setting(self):
        result = self.probe_draft()
        with self.connect() as conn:
            public = agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})
        self.assertNotIn(result["verification_token"], json.dumps(self.raw()))
        self.assertNotIn("pending", json.dumps(public))
        with self.connect() as conn, self.assertRaisesRegex(ValueError, "已使用"):
            agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})

    def test_receipt_cannot_be_used_for_another_project(self):
        result = self.probe_draft()
        with self.connect() as conn:
            conn.execute("INSERT INTO project VALUES('other-project',1,'fixture','2026-10-18')")
            raw = conn.execute("SELECT settings_json FROM app_agent_connection").fetchone()[0]
            conn.execute("INSERT INTO app_agent_connection VALUES('other-project',?,'2026-10-18')", (raw,))
            with self.assertRaisesRegex(ValueError, "不属于"):
                agent.save_settings(conn, "other-project", {"verification_token": result["verification_token"]})

    def test_receipt_expiry_and_changed_form_fields_are_rejected(self):
        result = self.probe_draft()
        with self.connect() as conn:
            with self.assertRaisesRegex(ValueError, "修改连接"):
                agent.save_settings(conn, self.project, {
                    "verification_token": result["verification_token"], "model": "replacement"})
            saved = agent._saved(conn, self.project)
            saved["_pending_probes"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
            agent._write(conn, self.project, saved)
            with self.assertRaisesRegex(ValueError, "过期"):
                agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})

    def test_receipt_rejects_tampered_parameters_and_credentials(self):
        for field, value in (("parameters", {"max_tokens": 6000}), ("user_agent", "changed-agent")):
            with self.subTest(field=field):
                result = self.probe_draft()
                with self.connect() as conn:
                    saved = agent._saved(conn, self.project)
                    saved["_pending_probes"][-1]["settings"][field] = value
                    agent._write(conn, self.project, saved)
                    with self.assertRaisesRegex(ValueError, "凭据或连接已变化"):
                        agent.save_settings(conn, self.project,
                                            {"verification_token": result["verification_token"]})

    def test_receipt_expires_when_active_main_changes(self):
        self.save_main()
        result = self.probe_draft()
        self.save_main(model="replacement-main")
        with self.connect() as conn, self.assertRaisesRegex(ValueError, "发生变化"):
            agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})

    def test_library_selection_reuses_key_without_generating(self):
        entry = self.library_model()
        count = len(Provider.calls)
        with self.connect() as conn:
            public = agent.save_settings(conn, self.project, {"mode": "library", "connection_id": entry["id"]})
        self.assertEqual(public["mode"], "library")
        self.assertTrue(public["configured"])
        self.assertEqual(public["model"], entry["model"])
        self.assertEqual(self.active()["api_key"], MAIN_KEY)
        self.assertEqual(len(Provider.calls), count)
        self.assertNotIn(MAIN_KEY, json.dumps(public))

    def test_responses_library_chat_does_not_inherit_main_or_independent_chat_parameters(self):
        main_parameters = {"temperature": 0.9, "max_tokens": 73, "thinking": {"type": "disabled"},
                           "seed": 41, "stop": ["旧输出结束"], "presence_penalty": 0.5,
                           "frequency_penalty": 0.3}
        self.save_main(parameters=main_parameters,
                       task_parameters={"summary": {"temperature": 1.6, "max_tokens": 97, "seed": 83}})
        self.save_verified()
        selected = self.library_model("responses-library-fixture", api_protocol="responses")
        count = len(Provider.calls)
        with self.connect() as conn:
            main_before = moyu_settings._saved(conn, self.project)
            with patch("moyu_agent_settings.prepare_chat_request", wraps=agent.prepare_chat_request) as prepare:
                public = agent.save_settings(conn, self.project, {
                    "mode": "library", "connection_id": selected["id"]})
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(prepare.call_args.kwargs, {
                "task": "assistant_chat", "response_format": "json", "max_tokens": 3000, "stream": True})
            config = agent.provider_settings(conn, self.project)
            self.assertEqual(moyu_settings._saved(conn, self.project), main_before)
            self.assertEqual(public["parameters"], {"temperature": 0.35, "max_tokens": 3000})
            self.assertEqual(public["independent_settings"]["parameters"]["thinking"], {"type": "disabled"})
            self.assertEqual(config["parameters"], {"temperature": 0.35})
            self.assertEqual(config["max_tokens"], 3000)
            self.assertEqual(config["task_models"], {})
            self.assertEqual(config["task_parameters"], {})
            self.assertEqual(config["task_max_tokens"], {})
            frozen = moyu_settings.frozen_provider_settings(
                conn, self.project, {name: value for name, value in config.items() if name != "api_key"})
        self.assertTrue(public["configured"])
        self.assertEqual(len(Provider.calls), count)
        result = call_chat_completion(
            frozen, [{"role": "user", "content": "synthetic assistant chat"}],
            task="assistant_chat", response_format="json", max_tokens=config["max_tokens"],
            stream=True, schema=AGENT_CHAT_SCHEMA)
        self.assertTrue(result["safe_to_apply"])
        self.assertEqual(json.loads(result["text"]), {"reply": "已读取已确认的设定。", "actions": []})
        call = Provider.calls[-1]
        self.assertEqual(call["path"], "/normal/responses")
        self.assertEqual(call["authorization"], "Bearer " + MAIN_KEY)
        self.assertEqual(call["payload"]["temperature"], 0.35)
        self.assertEqual(call["payload"]["max_output_tokens"], 3000)
        self.assertEqual(call["payload"]["text"], {"format": {"type": "json_object"}})
        self.assertTrue(call["payload"]["stream"])
        for field in ("thinking", "seed", "stop", "presence_penalty", "frequency_penalty", "messages"):
            self.assertNotIn(field, call["payload"])

    def test_incompatible_library_chat_configuration_is_rejected_before_save(self):
        self.save_verified()
        selected = self.library_model("responses-library-fixture", api_protocol="responses")
        before = self.raw()
        count = len(Provider.calls)
        original = agent._configuration
        def incompatible(conn, project, saved):
            config = original(conn, project, saved)
            return {**config, "parameters": {"seed": 41}} if saved["mode"] == "library" else config
        with self.connect() as conn, patch("moyu_agent_settings._configuration", side_effect=incompatible):
            with self.assertRaisesRegex(Exception, "Responses.*seed"):
                agent.save_settings(conn, self.project, {"mode": "library", "connection_id": selected["id"]})
        self.assertEqual(self.raw(), before)
        self.assertEqual(len(Provider.calls), count)

    def test_library_invalidated_source_blocks_real_requests(self):
        entry = self.library_model()
        with self.connect() as conn:
            agent.save_settings(conn, self.project, {"mode": "library", "connection_id": entry["id"]})
            payload = library._payload(conn, self.project)
            payload["connections"][0]["model"] = "tampered-model"
            moyu_settings._write_settings(conn, self.project, payload)
            self.assertFalse(agent.public_settings(conn, self.project)["configured"])
            with self.assertRaisesRegex(ValueError, "失效"):
                agent.provider_settings(conn, self.project)

    def test_library_rejects_missing_cross_project_and_unverified_ids(self):
        entry = self.library_model()
        with self.connect() as conn:
            for identifier in ("missing", "", [], entry["id"] + "-other"):
                with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                    agent.save_settings(conn, self.project, {"mode": "library", "connection_id": identifier})
            payload = library._payload(conn, self.project)
            payload["connections"][0]["_verified_signature"] = "invalid"
            moyu_settings._write_settings(conn, self.project, payload)
            with self.assertRaises(ValueError):
                agent.save_settings(conn, self.project, {"mode": "library", "connection_id": entry["id"]})

    def test_switching_modes_retains_verified_independent(self):
        self.save_main()
        self.save_verified()
        with self.connect() as conn:
            agent.save_settings(conn, self.project, {"mode": "inherit"})
            self.assertEqual(agent.provider_settings(conn, self.project)["api_key"], MAIN_KEY)
            restored = agent.save_settings(conn, self.project, {"mode": "independent"})
        self.assertTrue(restored["configured"])
        self.assertEqual(self.active()["api_key"], AGENT_KEY)

    def test_legacy_unverified_independent_is_visible_but_not_callable(self):
        with self.connect() as conn:
            payload, _config = agent._draft(conn, self.project, self.draft())
            payload["_credential_ref"] = moyu_settings._store_key(conn, self.project, AGENT_KEY)
            agent._write(conn, self.project, payload)
            public = agent.public_settings(conn, self.project)
            self.assertFalse(public["configured"])
            self.assertEqual(public["independent_settings"]["model"], "agent-fixture")
            with self.assertRaisesRegex(ValueError, "尚未通过测试"):
                agent.provider_settings(conn, self.project)
            result = agent.probe(conn, self.project)
            self.assertTrue(result["probe"]["success"])
            self.assertEqual(agent.provider_settings(conn, self.project)["api_key"], AGENT_KEY)

    def test_probe_records_dispatch_and_shared_quota(self):
        result = self.probe_draft()
        call = Provider.calls[-1]
        self.assertEqual(call["payload"]["max_tokens"], moyu_settings.PROBE_MAX_TOKENS)
        self.assertEqual(call["ledger"][0], "dispatched")
        self.assertEqual(call["ledger"][1]["connection_scope"], "agent_chat")
        with self.connect() as conn:
            ledger = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                  (result["ai_run_id"],)).fetchone()[0]
        self.assertEqual(ledger, "consumed")
        self.save_main(daily_limit=1)
        before = len(Provider.calls)
        with self.assertRaisesRegex(ValueError, "共享生成上限"):
            self.probe_draft()
        self.assertEqual(len(Provider.calls), before)

    def test_provider_interruption_retains_dispatch_and_does_not_replace_active_settings(self):
        self.save_verified()
        before = self.raw()
        count = len(Provider.calls)
        for dispatched in (False, True):
            with self.subTest(dispatched=dispatched):
                def interrupt(config, messages, **kwargs):
                    if dispatched:
                        prepared = agent.prepare_chat_request(
                            config, messages, task=kwargs["task"], max_tokens=kwargs["max_tokens"],
                            parameters=kwargs["parameters"], request_id=kwargs["request_id"])
                        kwargs["on_dispatch"]({**prepared["request_audit"], "dispatched": True})
                    raise RuntimeError("synthetic interruption")
                with patch("moyu_agent_settings.call_chat_completion", side_effect=interrupt):
                    result = self.probe_draft()
                self.assertFalse(result["probe"]["success"])
                self.assertEqual(result["probe"]["error_code"], "probe_interrupted")
                self.assertEqual(result["probe"]["billing_state"],
                                 "unknown" if dispatched else "not_dispatched")
                self.assertNotIn("verification_token", result)
                with self.connect() as conn:
                    record = conn.execute(
                        "SELECT a.status,q.status quota,a.consent_json FROM ai_run a "
                        "JOIN app_quota_reservation q ON q.ai_run_id=a.id WHERE a.id=?",
                        (result["ai_run_id"],)).fetchone()
                self.assertEqual(record["status"], "unknown" if dispatched else "failed")
                self.assertEqual(record["quota"], "unknown" if dispatched else "released")
                self.assertEqual(json.loads(record["consent_json"])["request_audit"]["dispatched"], dispatched)
                self.assertEqual(self.raw(), before)
        self.assertEqual(len(Provider.calls), count)

    def test_partial_usage_verifies_connection_but_keeps_charge_unknown(self):
        result = self.probe_draft("partial-usage")
        self.assertTrue(result["probe"]["success"])
        self.assertEqual(result["probe"]["billing_state"], "unknown")
        with self.connect() as conn:
            reservation = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                       (result["ai_run_id"],)).fetchone()[0]
            saved = agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})
        self.assertEqual(reservation, "unknown")
        self.assertTrue(saved["configured"])
        self.assertEqual(saved["last_probe"]["billing_state"], "unknown")

    def test_canceled_draft_releases_slot_without_key_persistence(self):
        cancel = threading.Event()
        cancel.set()
        with self.connect() as conn:
            result = agent.probe_draft(conn, self.project, self.draft(), cancel_event=cancel)
            status = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                  (result["ai_run_id"],)).fetchone()[0]
        self.assertEqual(status, "released")
        self.assertNotIn("verification_token", result)
        self.assertEqual(Provider.calls, [])
        self.assertFalse((self.root / "credentials").exists())

    def test_canceled_existing_probe_releases_shared_slot_without_sending(self):
        self.save_verified()
        count = len(Provider.calls)
        cancel = threading.Event()
        cancel.set()
        with self.connect() as conn:
            result = agent.probe(conn, self.project, cancel_event=cancel)
            status = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                  (result["ai_run_id"],)).fetchone()[0]
        self.assertFalse(result["probe"]["success"])
        self.assertEqual(result["probe"]["billing_state"], "not_dispatched")
        self.assertEqual(status, "released")
        self.assertEqual(len(Provider.calls), count)

    def test_inherited_probe_expires_when_main_changes_without_rewriting_evidence(self):
        self.save_main()
        with self.connect() as conn:
            result = agent.probe(conn, self.project)
        self.assertTrue(result["probe"]["success"])
        self.save_main(model="replacement-main")
        with self.connect() as conn:
            public = agent.public_settings(conn, self.project)
        self.assertTrue(public["probe_stale"])
        self.assertIsNone(public["last_probe"])
        self.assertEqual(self.raw()["last_probe"], result["probe"])

    def test_failed_protocol_and_identity_never_issue_receipt(self):
        for case, code in (("mismatch", "probe_model_mismatch"), ("extra", "probe_unexpected_response"),
                           ("truncated", "probe_incomplete_response"), ("reasoning", "probe_unexpected_reasoning"),
                           ("invalid-reasoning", "probe_unexpected_reasoning"), ("unauthorized", "provider_http_error")):
            with self.subTest(case=case):
                result = self.probe_draft(case)
                self.assertFalse(result["probe"]["success"])
                self.assertEqual(result["probe"]["error_code"], code)
                self.assertNotIn("verification_token", result)
                self.assertNotIn(AGENT_KEY, json.dumps(result))
                self.assertNotIn("fixture-private-reasoning", json.dumps(result))

    def test_punctuation_and_unreported_identity_are_accepted_precisely(self):
        for case in ("punctuation", "unreported"):
            with self.subTest(case=case):
                result = self.probe_draft(case)
                self.assertTrue(result["probe"]["success"])
                if case == "unreported":
                    self.assertEqual(result["probe"]["identity_status"], "unreported")
                    self.assertIsNone(result["probe"]["model_identity_match"])

    def test_transport_options_reach_inventory_probe_and_frozen_requests(self):
        options = {"provider": "deepseek", "custom_request_path": "/normal/custom/responses",
                   "models_path": "/inventory/custom/models", "user_agent": "MoyuFixture/2",
                   "api_protocol": "responses", "output_token_parameter": "max_completion_tokens", "include_usage": True}
        with self.connect() as conn:
            listed = agent.list_models(conn, self.project, {
                "base_url": self.base + "/normal/v1", "api_key": AGENT_KEY, **options})
        self.assertEqual(listed["endpoint"], self.base + "/inventory/custom/models")
        self.assertEqual(Provider.model_gets[-1]["user_agent"], "MoyuFixture/2")
        public = self.save_verified(base_url=self.base + "/normal/v1", **options,
                                    parameters={"temperature": 0, "max_tokens": 3000, "reasoning_effort": "none"})
        call = Provider.calls[-1]
        self.assertEqual(call["path"], "/normal/custom/responses")
        self.assertEqual(call["user_agent"], "MoyuFixture/2")
        self.assertIn("input", call["payload"])
        self.assertNotIn("messages", call["payload"])
        self.assertEqual(public["provider"], "deepseek")
        with self.connect() as conn:
            config = agent.provider_settings(conn, self.project)
            frozen = moyu_settings.frozen_provider_settings(
                conn, self.project, {name: value for name, value in config.items() if name != "api_key"})
        call_chat_completion(frozen, [{"role": "user", "content": "synthetic"}], task="agent_chat", max_tokens=123)
        self.assertEqual(Provider.calls[-1]["path"], "/normal/custom/responses")
        self.assertEqual(Provider.calls[-1]["payload"]["max_output_tokens"], 123)

    def test_responses_identity_and_chat_stream_compatibility_remain_independent(self):
        self.save_main(api_protocol="responses")
        self.save_verified(output_token_parameter="max_completion_tokens", include_usage=True)
        with self.connect() as conn:
            config = agent.provider_settings(conn, self.project)
            frozen = moyu_settings.frozen_provider_settings(
                conn, self.project, {name: value for name, value in config.items() if name != "api_key"})
            inherited = agent.save_settings(conn, self.project, {"mode": "inherit"})
            restored = agent.save_settings(conn, self.project, {"mode": "independent"})
        self.assertEqual(inherited["api_protocol"], "responses")
        self.assertEqual(restored["api_protocol"], "chat_completions")
        call_chat_completion(frozen, [{"role": "user", "content": "synthetic chat"}],
                             task="agent_chat", max_tokens=123, stream=True)
        payload = Provider.calls[-1]["payload"]
        self.assertEqual(payload["max_completion_tokens"], 123)
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        for case, success, identity in (("mismatch", False, "mismatch"), ("unreported", True, "unreported")):
            with self.subTest(case=case):
                result = self.probe_draft(case, api_protocol="responses",
                    parameters={"temperature": 0, "max_tokens": 3000, "reasoning_effort": "none"})
                self.assertEqual(result["probe"]["success"], success)
                self.assertEqual(result["probe"]["identity_status"], identity)
                self.assertTrue(Provider.calls[-1]["path"].endswith("/responses"))
                if not success:
                    self.assertNotIn("verification_token", result)

    def test_invalid_drafts_never_dispatch_or_overwrite(self):
        self.save_verified()
        before = self.raw()
        count = len(Provider.calls)
        for invalid in ({"parameters": {"temperature": -0.1}}, {"parameters": {"max_tokens": True}},
                        {"parameters": {"unknown": 1}}, {"mode": "other"}, {"api_protocol": "other"},
                        {"provider": "unknown"}, {"base_url": "http://example.com/v1"},
                        {"api_key": "invalid\nkey"}, {"clear_api_key": True},
                        {"custom_request_path": "//evil.invalid/path"}, {"models_path": "/v1/../secret"},
                        {"user_agent": "x\ninjected"}, {"include_usage": 1}):
            with self.subTest(invalid=invalid), self.connect() as conn, self.assertRaises(ValueError):
                agent.probe_draft(conn, self.project, self.draft(**invalid))
        self.assertEqual(len(Provider.calls), count)
        self.assertEqual(self.raw(), before)

    def test_inventory_is_read_only_and_never_reuses_main_key(self):
        self.save_main()
        with self.connect() as conn:
            before = "\n".join(conn.iterdump())
            result = agent.list_models(conn, self.project, {"base_url": self.base + "/normal"})
            self.assertEqual(before, "\n".join(conn.iterdump()))
            self.assertEqual(conn.total_changes, 0)
        self.assertEqual(set(result["models"]), {"agent-fixture", "other-fixture"})
        self.assertIsNone(Provider.model_gets[-1]["authorization"])
        self.assertEqual(Provider.calls, [])

    def test_inventory_saved_key_explicit_clear_and_protocol_are_read_only(self):
        self.save_main()
        self.save_verified(api_protocol="responses",
                           parameters={"temperature": 0, "max_tokens": 3000, "reasoning_effort": "none"})
        with self.connect() as conn:
            agent.save_settings(conn, self.project, {"mode": "inherit"})
        directory = self.root / "credentials"
        files = {path.name: path.read_bytes() for path in directory.iterdir()}
        count = len(Provider.calls)
        with self.connect() as conn:
            before = "\n".join(conn.iterdump())
            with patch("moyu_provider.list_models", return_value={
                    "models": ["agent-fixture"], "endpoint": self.base + "/normal/models"}) as listing:
                agent.list_models(conn, self.project, {})
                self.assertEqual(listing.call_args.args[0]["api_protocol"], "responses")
                self.assertEqual(listing.call_args.args[0]["api_key"], AGENT_KEY)
                agent.list_models(conn, self.project, {"api_protocol": "chat_completions"})
                self.assertEqual(listing.call_args.args[0]["api_protocol"], "chat_completions")
            for payload, expected in (({}, "Bearer " + AGENT_KEY),
                                      ({"api_key": MAIN_KEY}, "Bearer " + MAIN_KEY),
                                      ({"clear_api_key": True}, None), ({"api_key": ""}, None)):
                agent.list_models(conn, self.project, payload)
                self.assertEqual(Provider.model_gets[-1]["authorization"], expected)
            self.assertEqual(before, "\n".join(conn.iterdump()))
            self.assertEqual(conn.total_changes, 0)
        self.assertEqual(files, {path.name: path.read_bytes() for path in directory.iterdir()})
        self.assertEqual(len(Provider.calls), count)

    def test_inventory_cross_project_credentials_and_invalid_drafts_never_dispatch(self):
        self.save_verified()
        count = len(Provider.calls)
        with self.connect() as conn:
            conn.execute("INSERT INTO project VALUES('other-project',1,'fixture','2026-10-18')")
            raw = conn.execute("SELECT settings_json FROM app_agent_connection").fetchone()[0]
            conn.execute("INSERT INTO app_agent_connection VALUES('other-project',?,'2026-10-18')", (raw,))
            with self.assertRaisesRegex(ValueError, "其他作品"):
                agent.list_models(conn, "other-project", {})
            for payload in ({"base_url": self.base, "model": "unused"},
                            {"base_url": self.base, "parameters": {}},
                            {"base_url": self.base, "api_key": []},
                            {"base_url": self.base, "api_key": "invalid\nkey"},
                            {"base_url": self.base, "api_protocol": "unknown"},
                            {"base_url": self.base, "clear_api_key": "yes"},
                            {"base_url": self.base, "clear_api_key": True, "api_key": AGENT_KEY},
                            {"base_url": self.base, "user_agent": "非英文"},
                            {"base_url": self.base, "models_path": "//outside.invalid/models"}):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    agent.list_models(conn, self.project, payload)
        self.assertEqual(Provider.model_gets, [])
        self.assertEqual(len(Provider.calls), count)

    def test_saved_key_cannot_follow_changed_service(self):
        self.save_verified()
        with self.connect() as conn:
            for endpoint in ("https://synthetic.invalid/v1", self.base + "/different"):
                with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValueError, "服务地址已改变"):
                    agent.probe_draft(conn, self.project, {
                        "mode": "independent", "base_url": endpoint, "model": "agent-fixture"})
                with self.assertRaisesRegex(ValueError, "服务地址已改变"):
                    agent.list_models(conn, self.project, {"base_url": endpoint})
            for suffix in ("", "/", "/responses", "/chat/completions", "/models"):
                agent.list_models(conn, self.project, {"base_url": self.base + "/normal" + suffix})
                self.assertEqual(Provider.model_gets[-1]["authorization"], "Bearer " + AGENT_KEY)

    def test_previous_signatures_only_survive_default_new_transport(self):
        self.save_verified()
        with self.connect() as conn:
            saved = agent._saved(conn, self.project)
            config = agent.provider_settings(conn, self.project)
            fields = ("mode", "base_url", "model", "parameters", "max_tokens", "credential_fingerprint",
                      "output_token_parameter", "include_usage", "api_protocol")
            legacy = hashlib.sha256(json.dumps({name: config.get(name) for name in fields},
                sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            saved["_independent_signature"] = legacy
            saved["_probe_signature"] = legacy
            agent._write(conn, self.project, saved)
            self.assertTrue(agent.public_settings(conn, self.project)["configured"])
            for field, value in (("provider", "openai"), ("custom_request_path", "/normal/custom"),
                                 ("models_path", "/normal/models"), ("user_agent", "changed")):
                changed = {**saved, field: value}
                agent._write(conn, self.project, changed)
                self.assertFalse(agent.public_settings(conn, self.project)["configured"], field)
            agent._write(conn, self.project, saved)

    def test_oldest_probe_signature_migrates_without_writes_and_rejects_new_options(self):
        self.save_verified()
        with self.connect() as conn:
            config = agent.provider_settings(conn, self.project)
            fields = ("mode", "base_url", "model", "parameters", "max_tokens", "credential_fingerprint")
            original = {name: config.get(name) for name in fields}
            original["output_token_parameter"] = None
            signature = hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False,
                allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            saved = agent._saved(conn, self.project)
            for field in ("_independent_probe", "_independent_signature", "output_token_parameter",
                          "include_usage", "api_protocol", *agent.moyu_provider.TRANSPORT_DEFAULTS):
                saved.pop(field, None)
            saved["_probe_signature"] = signature
            agent._write(conn, self.project, saved)
            changes = conn.total_changes
            public = agent.public_settings(conn, self.project)
            self.assertEqual(changes, conn.total_changes)
            self.assertTrue(public["configured"])
            self.assertFalse(public["probe_stale"])
            self.assertTrue(agent.provider_settings(conn, self.project)["api_key"])
            agent._write(conn, self.project, {**saved, "include_usage": True})
            self.assertFalse(agent.public_settings(conn, self.project)["configured"])

    def test_two_option_probe_signature_only_remains_valid_for_chat(self):
        self.save_verified(output_token_parameter="max_completion_tokens", include_usage=True)
        with self.connect() as conn:
            config = agent.provider_settings(conn, self.project)
            fields = ("mode", "base_url", "model", "parameters", "max_tokens",
                      "credential_fingerprint", "output_token_parameter", "include_usage")
            signature = hashlib.sha256(json.dumps({name: config.get(name) for name in fields},
                sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            saved = agent._saved(conn, self.project)
            for field in ("_independent_probe", "_independent_signature", "api_protocol",
                          *agent.moyu_provider.TRANSPORT_DEFAULTS):
                saved.pop(field, None)
            saved["_probe_signature"] = signature
            agent._write(conn, self.project, saved)
            changes = conn.total_changes
            public = agent.public_settings(conn, self.project)
            self.assertEqual(changes, conn.total_changes)
            self.assertTrue(public["configured"])
            self.assertEqual(public["api_protocol"], "chat_completions")
            agent._write(conn, self.project, {**saved, "api_protocol": "responses"})
            changed = agent.public_settings(conn, self.project)
            self.assertTrue(changed["probe_stale"])
            self.assertFalse(changed["configured"])

    def test_replaced_receipt_key_file_and_invalid_pending_data_cannot_activate(self):
        result = self.probe_draft()
        with self.connect() as conn:
            saved = agent._saved(conn, self.project)
            reference = saved["_pending_probes"][0]["settings"]["_credential_ref"]
        path = self.root / "credentials" / (reference + ".json")
        path.write_text(json.dumps({"api_key": MAIN_KEY}), encoding="utf-8")
        with self.connect() as conn:
            with self.assertRaisesRegex(ValueError, "凭据或连接已变化"):
                agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})
            for pending in (None, {}, "invalid"):
                agent._write(conn, self.project, {**saved, "_pending_probes": pending})
                with self.subTest(pending=pending), self.assertRaisesRegex(ValueError, "过期"):
                    agent.save_settings(conn, self.project, {"verification_token": result["verification_token"]})
        self.assertEqual(self.raw()["mode"], "inherit")

    def test_library_connection_cannot_be_deleted_while_used_by_agent(self):
        primary = self.library_model("main-library-fixture")
        selected = self.library_model("secondary-library-fixture")
        count = len(Provider.calls)
        with self.connect() as conn:
            agent.save_settings(conn, self.project, {"mode": "library", "connection_id": selected["id"]})
            with self.assertRaisesRegex(ValueError, "总管正在使用"):
                library.remove_connection(conn, self.project, selected["id"])
            self.assertEqual(agent.provider_settings(conn, self.project)["model"], selected["model"])
            agent.save_settings(conn, self.project, {"mode": "inherit"})
            library.remove_connection(conn, self.project, selected["id"])
            self.assertEqual([item["id"] for item in library.public_connections(conn, self.project)["connections"]],
                             [primary["id"]])
        self.assertEqual(len(Provider.calls), count)

    def test_probe_race_does_not_replace_new_active_configuration(self):
        self.save_verified()
        results, errors = [], []
        def work():
            try:
                with self.connect() as conn:
                    results.append(agent.probe_draft(conn, self.project, self.draft("held")))
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=work)
        self.probe_threads.append(thread)
        thread.start()
        self.assertTrue(Provider.started.wait(timeout=3))
        with self.connect() as conn:
            agent.save_settings(conn, self.project, {"mode": "inherit"})
        Provider.release.set()
        thread.join(timeout=5)
        self.assertFalse(errors, errors)
        self.assertFalse(results[0]["probe"]["success"])
        self.assertTrue(results[0]["probe"]["configuration_stale"])
        self.assertNotIn("verification_token", results[0])
        self.assertEqual(self.raw()["mode"], "inherit")

    def test_key_files_private_and_cross_project_references_rejected(self):
        self.save_verified()
        directory = self.root / "credentials"
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        files = list(directory.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        with self.connect() as conn:
            conn.execute("INSERT INTO project VALUES('other-project',1,'fixture','2026-10-18')")
            raw = conn.execute("SELECT settings_json FROM app_agent_connection").fetchone()[0]
            conn.execute("INSERT INTO app_agent_connection VALUES('other-project',?,'2026-10-18')", (raw,))
            with self.assertRaisesRegex(ValueError, "其他作品"):
                agent.public_settings(conn, "other-project")
        files[0].chmod(0o644)
        with self.connect() as conn, self.assertRaisesRegex(ValueError, "权限"):
            agent.public_settings(conn, self.project)
        files[0].chmod(0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
