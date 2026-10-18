"""Model transport contracts using synthetic projects and real loopback HTTP."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_backups as backups
import moyu_model_connections as connections
import moyu_provider as provider
import moyu_settings as settings
import server


KEY = "synthetic-transport-fixture-private-value"
PROJECT = "transport-fixture-project"
BRANCH = "transport-fixture-main"
ACTOR = "transport-fixture-author"
TRANSPORT_FIELDS = ("provider", "custom_request_path", "models_path", "user_agent")
CUSTOM = {
    "provider": "deepseek",
    "custom_request_path": "/gateway/generate",
    "models_path": "/catalog/models",
    "user_agent": "Moyu-Transport-Fixture/1.0",
}


class Provider(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.calls.append({
            "method": "GET", "path": self.path,
            "user_agent": self.headers.get("User-Agent"),
            "authorization": self.headers.get("Authorization"),
        })
        self.respond({"data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-a"}]})

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.server.calls.append({
            "method": "POST", "path": self.path, "payload": payload,
            "user_agent": self.headers.get("User-Agent"),
            "authorization": self.headers.get("Authorization"),
        })
        text = {
            "/probe/punctuation": " \n\u201c\u8fde\u63a5\u6b63\u5e38\u3002\u201d \t",
            "/probe/long": "\u8fde\u63a5\u6b63\u5e38\u3002" + "additional text " * 40,
        }.get(self.path, "\u8fde\u63a5\u6b63\u5e38")
        model = "another-model" if self.path == "/probe/mismatch" else payload["model"]
        if "input" in payload:
            response = {
                "id": "resp-transport-fixture", "object": "response",
                "status": "incomplete" if self.path == "/probe/truncated" else "completed",
                "model": model,
                "output": [{
                    "id": "msg-transport-fixture", "type": "message", "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }],
                "usage": {"input_tokens": 13, "output_tokens": 7, "total_tokens": 20},
            }
            if self.path == "/probe/truncated":
                response["incomplete_details"] = {"reason": "max_output_tokens"}
        else:
            response = {
                "model": model,
                "choices": [{
                    "message": {"content": text},
                    "finish_reason": "length" if self.path == "/probe/truncated" else "stop",
                }],
                "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20},
            }
        self.respond(response)

    def respond(self, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def log_message(self, *_args: object) -> None:
        return


class QuietApp(server.Handler):
    def log_message(self, *_args: object) -> None:
        return


class ModelTransportSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.providers = []
        for _ in range(2):
            fixture = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
            fixture.daemon_threads = True
            fixture.calls = []
            thread = threading.Thread(target=fixture.serve_forever, daemon=True)
            thread.start()
            cls.providers.append((fixture, thread))
        cls.bases = [f"http://127.0.0.1:{fixture.server_port}/v1"
                     for fixture, _thread in cls.providers]

    @classmethod
    def tearDownClass(cls) -> None:
        for fixture, thread in cls.providers:
            fixture.shutdown()
            fixture.server_close()
            thread.join(timeout=3)

    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="moyu-transport-fixture-")))
        self.database = self.root / "book.sqlite3"
        self.stack.enter_context(patch.dict(os.environ, {
            "MOYU_CREDENTIALS_DIR": str(self.root / "credentials"),
            "MOYU_AI_API_KEY": "", "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "",
            "MOYU_AI_DAILY_QUOTA": "100", "MOYU_AI_TEMPERATURE": "0.65",
        }))
        for field, value in (("DB_PATH", self.database), ("PROJECT_ID", PROJECT),
                             ("BRANCH_ID", BRANCH), ("ACTOR_ID", ACTOR)):
            self.stack.enter_context(patch.object(server, field, value))
        self.worker = self.stack.enter_context(patch.object(
            server, "start_ai_worker", side_effect=AssertionError("worker forbidden")))
        for fixture, _thread in self.providers:
            fixture.calls.clear()
        self.api = None
        server.init_db()

    def tearDown(self) -> None:
        if self.api is not None:
            self.api.shutdown()
            self.api.server_close()
            self.api_thread.join(timeout=3)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def config(self, **overrides) -> dict:
        return {"base_url": self.bases[0], "model": "model-a", "api_key": KEY, **overrides}

    def payload(self) -> dict:
        with self.connect() as conn:
            return settings._saved(conn, PROJECT) or {}

    def probe(self, **overrides) -> dict:
        with self.connect() as conn:
            return connections.probe_connection(conn, PROJECT, {
                "base_url": self.bases[0], "model": "model-a", "api_key": KEY, **overrides,
            })

    def save(self, result: dict, **overrides) -> dict:
        with self.connect() as conn:
            return connections.save_connection(conn, PROJECT, {
                "verification_token": result["verification_token"], **overrides,
            })

    def saved(self, **overrides) -> dict:
        return self.save(self.probe(**overrides))["connection"]

    def active(self, task: str = "continue") -> dict:
        with self.connect() as conn:
            return settings.task_provider_settings(settings.provider_settings(conn, PROJECT), task)

    def assign(self, identifier: str) -> dict:
        with self.connect() as conn:
            return settings.save_settings(conn, PROJECT, {"default_connection_id": identifier})

    def request(self, method: str, path: str, body=None) -> tuple[int, dict]:
        if self.api is None:
            self.api = ThreadingHTTPServer(("127.0.0.1", 0), QuietApp)
            self.api.daemon_threads = True
            self.api_thread = threading.Thread(target=self.api.serve_forever, daemon=True)
            self.api_thread.start()
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.api.server_port}" + path, data=raw, method=method,
            headers={"Content-Type": "application/json", "X-Moyu-Project": PROJECT},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def assert_private(self, value, *tokens: str) -> None:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self.assertFalse(any(secret in text for secret in (KEY, *tokens)),
                         "a synthetic secret escaped the public boundary")

    def test_defaults_and_supported_provider_choices_are_normalized(self) -> None:
        defaults = settings._connection_options({})
        self.assertEqual(defaults["provider"], "openai_compatible")
        for field in TRANSPORT_FIELDS[1:]:
            self.assertEqual(defaults[field], "")
        for name in ("openai_compatible", "openai", "deepseek", "qwen", "siliconflow", "ollama"):
            with self.subTest(provider=name):
                options = settings._connection_options({"provider": name})
                self.assertEqual(options["provider"], name)
                request = provider.prepare_chat_request(
                    self.config(**options), [{"role": "user", "content": "test"}])
                self.assertEqual(request["endpoint"], self.bases[0] + "/chat/completions")
        for invalid in ("anthropic", "unknown", None, [], 1):
            with self.subTest(provider=invalid), self.assertRaises(ValueError):
                settings._connection_options({"provider": invalid})

    def test_copied_endpoint_suffixes_are_removed_before_model_discovery(self) -> None:
        for suffix in ("", "/", "/models", "/models/", "/chat/completions", "/responses"):
            with self.subTest(suffix=suffix):
                result = provider.list_models(self.config(base_url=self.bases[0] + suffix))
                self.assertEqual(result["endpoint"], self.bases[0] + "/models")
                self.assertEqual(result["models"], ["model-a", "model-b"])
                self.assertEqual(self.providers[0][0].calls[-1]["path"], "/v1/models")
                self.assertEqual(self.providers[0][0].calls[-1]["user_agent"], "Moyu/1")
                request = provider.prepare_chat_request(
                    self.config(base_url=self.bases[0] + suffix),
                    [{"role": "user", "content": "test"}])
                self.assertEqual(request["endpoint"], self.bases[0] + "/chat/completions")

    def test_root_service_address_remains_an_explicit_base_without_guessed_version(self) -> None:
        base = self.bases[0].removesuffix("/v1")
        result = provider.list_models(self.config(base_url=base))
        self.assertEqual(result["endpoint"], base + "/models")
        self.assertEqual(self.providers[0][0].calls[-1]["path"], "/models")

    def test_custom_model_path_and_user_agent_reach_real_http(self) -> None:
        before = self.payload()
        with self.connect() as conn:
            result = connections.list_connection_models(
                conn, PROJECT, {"base_url": self.bases[0], "api_key": KEY, **CUSTOM})
            self.assertEqual(conn.total_changes, 0)
        call = self.providers[0][0].calls[-1]
        self.assertEqual(result["endpoint"], self.bases[0].removesuffix("/v1") + "/catalog/models")
        self.assertEqual(call["path"], "/catalog/models")
        self.assertEqual(call["user_agent"], CUSTOM["user_agent"])
        self.assertTrue(call["authorization"] == "Bearer " + KEY)
        self.assertEqual(self.payload(), before)
        self.assertFalse((self.root / "credentials").exists())
        self.assert_private(result)

    def test_custom_generation_path_and_user_agent_apply_to_both_protocols(self) -> None:
        for protocol in ("chat_completions", "responses"):
            with self.subTest(protocol=protocol):
                config = self.config(**CUSTOM, api_protocol=protocol)
                messages = [{"role": "user", "content": "synthetic writing"}]
                prepared = provider.prepare_chat_request(config, messages, max_tokens=111)
                generated = provider.call_chat_completion(config, messages, max_tokens=111)
                call = self.providers[0][0].calls[-1]
                self.assertEqual(call["path"], "/gateway/generate")
                self.assertEqual(call["user_agent"], CUSTOM["user_agent"])
                self.assertTrue(call["authorization"] == "Bearer " + KEY)
                self.assertTrue(generated["safe_to_apply"])
                self.assertEqual(call["payload"], prepared["payload"])
                self.assertEqual(generated["request_audit"]["endpoint"], prepared["endpoint"])
                self.assertEqual(generated["request_audit"]["user_agent"], CUSTOM["user_agent"])
                self.assertEqual(prepared["request_audit"]["user_agent"], CUSTOM["user_agent"])
                self.assertIn("input" if protocol == "responses" else "messages", call["payload"])
                self.assert_private(generated)

    def test_invalid_paths_and_headers_fail_before_any_http(self) -> None:
        invalid_paths = (
            "relative/path", "//other.invalid/path", "https://other.invalid/path",
            "/path?secret=value", "/path#fragment", "/../other", "/one/../other",
            "/one/./other", "/path\r\nX-Injected: value", "/one\\other",
            "/%2fother.invalid/path", "/%2e%2e/other", "/one/%2E/other",
            "/path%3Fsecret=value", "/path%23fragment", "/path%0D%0AX-Injected:value",
        )
        for field in ("custom_request_path", "models_path"):
            for path in invalid_paths:
                with self.subTest(field=field, path=repr(path)), self.assertRaises(ValueError):
                    settings._connection_options({field: path})
        for value in ("bad\r\nAuthorization: injected", "bad\nvalue", [], 1):
            with self.subTest(user_agent=repr(value)), self.assertRaises(ValueError):
                settings._connection_options({"user_agent": value})
        self.assertEqual(self.providers[0][0].calls, [])

    def test_raw_provider_rejects_unsafe_overrides_without_a_settings_round_trip(self) -> None:
        for changes in (
            {"custom_request_path": "//other.invalid/generate"},
            {"models_path": "https://other.invalid/models"},
            {"user_agent": "bad\r\nX-Injected: value"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                provider.list_models(self.config(**changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                provider.prepare_chat_request(
                    self.config(**changes), [{"role": "user", "content": "test"}])
        self.assertEqual(self.providers[0][0].calls, [])

    def test_short_punctuation_reply_is_accepted_with_larger_reasoning_budget(self) -> None:
        result = self.probe(custom_request_path="/probe/punctuation")
        self.assertTrue(result["probe"]["success"])
        self.assertIn("verification_token", result)
        self.assertEqual(settings.PROBE_MAX_TOKENS, 1024)
        self.assertEqual(self.providers[0][0].calls[-1]["payload"]["max_tokens"], 1024)
        self.assertTrue(self.save(result)["connection"]["verified"])

    def test_probe_still_rejects_truncation_model_mismatch_and_long_reply(self) -> None:
        for path in ("/probe/truncated", "/probe/mismatch", "/probe/long"):
            for protocol in ("chat_completions", "responses"):
                with self.subTest(path=path, protocol=protocol):
                    result = self.probe(custom_request_path=path, api_protocol=protocol)
                    self.assertFalse(result["probe"]["success"])
                    self.assertNotIn("verification_token", result)
        self.assertEqual(self.payload().get("connections", []), [])

    def test_reusing_a_saved_key_can_discover_and_add_a_distinct_model(self) -> None:
        original = self.saved(**CUSTOM)
        original_entry = copy.deepcopy(self.payload()["connections"][0])
        files_before = set((self.root / "credentials").iterdir())
        before = self.payload()
        with self.connect() as conn:
            listed = connections.list_connection_models(conn, PROJECT, {
                "source_connection_id": original["id"],
                "base_url": self.bases[0] + "/models", **CUSTOM,
            })
        self.assertEqual(self.payload(), before)
        self.assertEqual(set((self.root / "credentials").iterdir()), files_before)
        self.assertEqual(listed["models"], ["model-a", "model-b"])
        self.assertTrue(self.providers[0][0].calls[-1]["authorization"] == "Bearer " + KEY)
        result = self.probe(source_connection_id=original["id"], api_key=None, model="model-b", **CUSTOM)
        self.assertTrue(result["probe"]["success"])
        new = self.save(result)["connection"]
        self.assertNotEqual(new["id"], original["id"])
        self.assertEqual(new["model"], "model-b")
        self.assertTrue(new["api_key_configured"])
        self.assertTrue(new["verified"])
        self.assertEqual(self.payload()["connections"][0], original_entry)
        self.assertEqual(len(self.payload()["connections"]), 2)
        self.assert_private(listed)
        self.assert_private(result)
        self.assert_private(new, result["verification_token"])

    def test_reused_key_cannot_change_service_or_mix_edit_and_source_identity(self) -> None:
        original = self.saved()
        before = self.payload()
        before_calls = sum(len(fixture.calls) for fixture, _thread in self.providers)
        for changes in (
            {"base_url": self.bases[1]},
            {"base_url": self.bases[0].replace("127.0.0.1", "localhost")},
            {"base_url": self.bases[0].replace("/v1", "/v2")},
            {"id": original["id"]},
            {"source_connection_id": "missing-connection"},
        ):
            draft = {
                "source_connection_id": original["id"], "base_url": self.bases[0],
                **changes,
            }
            with self.subTest(changes=changes), self.connect() as conn, self.assertRaises(ValueError):
                connections.list_connection_models(conn, PROJECT, draft)
            with self.subTest(changes=changes), self.connect() as conn, self.assertRaises(ValueError):
                connections.probe_connection(conn, PROJECT, {**draft, "model": "model-b"})
        other_project = "transport-fixture-other-book"
        with self.connect() as conn, server.workspace.use_scope(
                other_project, "transport-fixture-other-main", "transport-fixture-other-author"):
            server.ensure_seed(conn)
        with self.connect() as conn, self.assertRaises(ValueError):
            connections.list_connection_models(conn, other_project, {
                "source_connection_id": original["id"], "base_url": self.bases[0],
            })
        self.assertEqual(self.payload(), before)
        self.assertEqual(sum(len(fixture.calls) for fixture, _thread in self.providers), before_calls)

    def test_save_can_atomically_make_the_verified_connection_default(self) -> None:
        result = self.probe(**CUSTOM)
        saved = self.save(result, use_as_default=True)
        connection = saved["connection"]
        self.assertEqual(saved["settings"]["default_connection_id"], connection["id"])
        self.assertTrue(saved["settings"]["configured"])
        self.assertTrue(saved["settings"]["connection_workflow"])
        config = self.active()
        self.assertEqual(config["connection_id"], connection["id"])
        for field in TRANSPORT_FIELDS:
            self.assertEqual(config[field], CUSTOM[field])
        provider.call_chat_completion(config, [{"role": "user", "content": "actual default use"}])
        self.assertEqual(self.providers[0][0].calls[-1]["path"], "/gateway/generate")
        self.assertEqual(self.providers[0][0].calls[-1]["user_agent"], CUSTOM["user_agent"])
        self.assert_private(saved, result["verification_token"])

    def test_default_assignment_failure_does_not_consume_receipt_or_insert_connection(self) -> None:
        with self.connect() as conn:
            settings.save_settings(conn, PROJECT, {
                "base_url": self.bases[0], "model": "model-a", "api_key": KEY,
                "parameters": {"temperature": 0, "max_tokens": 3000,
                               "thinking": {"type": "disabled"}},
            })
        result = self.probe(api_protocol="responses")
        self.assertTrue(result["probe"]["success"])
        with self.connect() as conn:
            before = copy.deepcopy(settings._saved(conn, PROJECT))
            with self.assertRaises(ValueError):
                connections.save_connection(conn, PROJECT, {
                    "verification_token": result["verification_token"], "use_as_default": True,
                })
            self.assertEqual(settings._saved(conn, PROJECT), before)
        self.assertTrue(self.save(result)["connection"]["verified"])
        self.assertEqual(len(self.payload()["connections"]), 1)

    def test_save_without_default_flag_preserves_an_existing_default(self) -> None:
        first = self.saved()
        self.assign(first["id"])
        second = self.saved(model="model-b", **CUSTOM)
        self.assertEqual(self.active()["connection_id"], first["id"])
        self.assertNotEqual(first["id"], second["id"])

    def test_default_flag_is_strictly_boolean_and_invalid_flag_keeps_receipt(self) -> None:
        result = self.probe()
        for flag in ("true", 1, [], None):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.save(result, use_as_default=flag)
        self.assertTrue(self.save(result, use_as_default=True)["settings"]["configured"])

    def test_previous_three_option_signatures_remain_usable_only_with_default_transport(self) -> None:
        for protocol in ("chat_completions", "responses"):
            with self.subTest(protocol=protocol):
                saved = self.saved(api_protocol=protocol, output_token_parameter="max_completion_tokens",
                                   include_usage=True)
                with self.connect() as conn:
                    payload = settings._saved(conn, PROJECT)
                    entry = next(row for row in payload["connections"] if row["id"] == saved["id"])
                    old_fields = {
                        "base_url": entry["base_url"], "model": entry["model"],
                        "credential_fingerprint": hashlib.sha256(KEY.encode()).hexdigest(),
                        "api_protocol": protocol, "output_token_parameter": "max_completion_tokens",
                        "include_usage": True,
                    }
                    entry["_verified_signature"] = hashlib.sha256(json.dumps(
                        old_fields, sort_keys=True, ensure_ascii=False, allow_nan=False,
                        separators=(",", ":")).encode()).hexdigest()
                    for field in TRANSPORT_FIELDS:
                        entry.pop(field, None)
                    settings._write_settings(conn, PROJECT, payload)
                    public = connections.public_connections(conn, PROJECT)
                    current = next(row for row in public["connections"] if row["id"] == saved["id"])
                    self.assertTrue(current["verified"])
                self.assign(saved["id"])
                self.assertEqual(self.active()["api_protocol"], protocol)
                baseline = self.payload()
                for field, value in CUSTOM.items():
                    with self.subTest(field=field), self.connect() as conn:
                        tampered = copy.deepcopy(baseline)
                        entry = next(row for row in tampered["connections"] if row["id"] == saved["id"])
                        entry[field] = value
                        settings._write_settings(conn, PROJECT, tampered)
                        public = connections.public_connections(conn, PROJECT)
                        current = next(row for row in public["connections"] if row["id"] == saved["id"])
                        self.assertFalse(current["verified"])
                    with self.assertRaises(ValueError):
                        self.active()
                with self.connect() as conn:
                    settings._write_settings(conn, PROJECT, baseline)

    def test_pending_receipt_binds_every_transport_field(self) -> None:
        replacements = {
            "provider": "qwen", "custom_request_path": "/other/generate",
            "models_path": "/other/models", "user_agent": "Other-Transport/1",
        }
        for field, value in replacements.items():
            with self.subTest(field=field):
                result = self.probe(**CUSTOM)
                with self.connect() as conn:
                    payload = settings._saved(conn, PROJECT)
                    payload["_pending_connection_probes"][-1][field] = value
                    settings._write_settings(conn, PROJECT, payload)
                with self.assertRaises(ValueError):
                    self.save(result, use_as_default=True)
        self.assertEqual(self.payload().get("connections", []), [])

    def test_previous_three_option_pending_receipt_survives_upgrade(self) -> None:
        result = self.probe(api_protocol="responses", output_token_parameter="max_completion_tokens",
                            include_usage=True)
        with self.connect() as conn:
            payload = settings._saved(conn, PROJECT)
            receipt = payload["_pending_connection_probes"][-1]
            old_fields = {
                "base_url": receipt["base_url"], "model": receipt["model"],
                "credential_fingerprint": hashlib.sha256(KEY.encode()).hexdigest(),
                "api_protocol": "responses", "output_token_parameter": "max_completion_tokens",
                "include_usage": True,
            }
            receipt["_verified_signature"] = hashlib.sha256(json.dumps(
                old_fields, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":")).encode()).hexdigest()
            for field in TRANSPORT_FIELDS:
                receipt.pop(field, None)
            settings._write_settings(conn, PROJECT, payload)
        saved = self.save(result, use_as_default=True)
        self.assertTrue(saved["connection"]["verified"])
        self.assertEqual(self.active()["api_protocol"], "responses")

    def test_queued_request_freezes_transport_and_rejects_changed_saved_transport(self) -> None:
        saved = self.save(self.probe(**CUSTOM), use_as_default=True)["connection"]
        with self.connect() as conn:
            context, _ = server.ai_context(conn, server.chapter_rows(conn)[0]["id"])
            job = server.create_ai_job(conn, "continue", context, "", "text")
            run = conn.execute("SELECT consent_json,prompt_hash FROM ai_run WHERE id=?",
                               (job["ai_run_id"],)).fetchone()
            consent = json.loads(run["consent_json"])
            snapshot = consent["provider_config"]
            self.assertNotIn("api_key", snapshot)
            self.assert_private(snapshot)
            for field in TRANSPORT_FIELDS:
                self.assertEqual(snapshot[field], CUSTOM[field])
            config = server.retry_provider_config(conn, snapshot)
            frozen = server.prepare_frozen_ai_request(
                "continue", "text", 3000, config, consent["request_audit"]["payload"])
            server.validate_frozen_request_audit(consent["request_audit"], frozen, run["prompt_hash"])
            self.assertEqual(frozen["endpoint"], self.bases[0].removesuffix("/v1") + "/gateway/generate")
            self.assertEqual(frozen["request_audit"]["user_agent"], CUSTOM["user_agent"])
            provider.call_chat_completion(config, frozen["messages"], max_tokens=3000)
            self.assertEqual(self.providers[0][0].calls[-1]["path"], "/gateway/generate")
            self.assertEqual(self.providers[0][0].calls[-1]["user_agent"], CUSTOM["user_agent"])
            payload = settings._saved(conn, PROJECT)
            entry = next(row for row in payload["connections"] if row["id"] == saved["id"])
            entry["custom_request_path"] = "/changed/generate"
            settings._write_settings(conn, PROJECT, payload)
            with self.assertRaises(ValueError):
                server.retry_provider_config(conn, snapshot)
        self.worker.assert_not_called()

    def test_frozen_audit_accepts_old_default_user_agent_but_never_loses_custom_user_agent(self) -> None:
        messages = [{"role": "user", "content": "historical request"}]
        for user_agent in ("", CUSTOM["user_agent"]):
            with self.subTest(user_agent=user_agent):
                config = self.config(user_agent=user_agent)
                prepared = provider.prepare_chat_request(config, messages)
                audit = copy.deepcopy(prepared["request_audit"])
                old_audit = {name: value for name, value in audit.items() if name != "user_agent"}
                if not user_agent:
                    server.validate_frozen_request_audit(
                        old_audit, prepared, audit["request_sha256"])
                else:
                    with self.assertRaises(ValueError):
                        server.validate_frozen_request_audit(
                            old_audit, prepared, audit["request_sha256"])
                audit["user_agent"] = "Changed-Agent/1"
                with self.assertRaises(ValueError):
                    server.validate_frozen_request_audit(audit, prepared, audit["request_sha256"])

    def test_legacy_snapshot_rejects_path_or_user_agent_changes_before_secret_reuse(self) -> None:
        with self.connect() as conn:
            settings.save_settings(conn, PROJECT, {
                "base_url": self.bases[0], "model": "model-a", "api_key": KEY, **CUSTOM,
            })
            snapshot = {name: value for name, value in
                        settings.provider_settings(conn, PROJECT).items() if name != "api_key"}
            self.assertTrue(server.retry_provider_config(conn, snapshot)["api_key"] == KEY)
            baseline = copy.deepcopy(settings._saved(conn, PROJECT))
            for field, value in (
                ("custom_request_path", "/different/generate"), ("user_agent", "Different-Agent/1"),
                ("models_path", "/different/models"), ("provider", "qwen"),
            ):
                with self.subTest(field=field):
                    changed = copy.deepcopy(baseline)
                    changed[field] = value
                    settings._write_settings(conn, PROJECT, changed)
                    with self.assertRaises(ValueError):
                        server.retry_provider_config(conn, snapshot)
            settings._write_settings(conn, PROJECT, baseline)

    def test_backup_keeps_transport_but_removes_keys_verification_and_default_use(self) -> None:
        saved = self.save(self.probe(**CUSTOM), use_as_default=True)["connection"]
        pending = self.probe(model="model-b", **CUSTOM)
        with self.connect() as conn:
            captured = backups.capture(conn, PROJECT)
            self.assert_private(captured, pending["verification_token"])
            portable = json.loads(captured["tables"]["app_model_settings"][0]["settings_json"])
            self.assertEqual(portable["default_connection_id"], "")
            self.assertEqual(portable["task_connections"], {})
            self.assertNotIn("_pending_connection_probes", portable)
            entry = next(row for row in portable["connections"] if row["id"] == saved["id"])
            for field in TRANSPORT_FIELDS:
                self.assertEqual(entry[field], CUSTOM[field])
            self.assertIsNone(entry.get("_credential_ref"))
            self.assertIsNone(entry.get("last_probe"))
            self.assertNotIn("_verified_signature", entry)
            restored_project = "transport-fixture-restored"
            backups.validate_payload(conn, captured)
            backups.transplant(conn, captured, restored_project)
        with self.connect() as conn:
            public = settings.public_settings(conn, restored_project)
            self.assertFalse(public["configured"])
            restored = public["connections"][0]
            self.assertFalse(restored["verified"])
            self.assertFalse(restored["api_key_configured"])
            for field in TRANSPORT_FIELDS:
                self.assertEqual(restored[field], CUSTOM[field])
            self.assert_private("\n".join(conn.iterdump()), pending["verification_token"])

    def test_http_configuration_flow_reuses_key_and_enables_verified_default(self) -> None:
        first = self.saved(**CUSTOM)
        status, listed = self.request("POST", "/api/ai/connections/models", {
            "source_connection_id": first["id"], "base_url": self.bases[0], **CUSTOM,
        })
        self.assertEqual(status, 200)
        self.assertEqual(listed["models"], ["model-a", "model-b"])
        self.assertEqual(self.providers[0][0].calls[-1]["path"], "/catalog/models")
        status, probed = self.request("POST", "/api/ai/connections/probe", {
            "source_connection_id": first["id"], "base_url": self.bases[0],
            "model": "model-b", **CUSTOM,
        })
        self.assertEqual(status, 200)
        self.assertTrue(probed["probe"]["success"])
        self.assertEqual(self.providers[0][0].calls[-1]["path"], "/gateway/generate")
        status, saved = self.request("POST", "/api/ai/connections", {
            "verification_token": probed["verification_token"], "use_as_default": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(saved["settings"]["configured"])
        self.assertNotEqual(saved["connection"]["id"], first["id"])
        self.assertEqual(saved["settings"]["default_connection_id"], saved["connection"]["id"])
        self.assertEqual(self.active()["model"], "model-b")
        for value in (listed, probed, saved):
            self.assert_private(value)
        self.assert_private(saved, probed["verification_token"])
        status, public = self.request("GET", "/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertEqual(public["settings"]["user_agent"], CUSTOM["user_agent"])
        self.assert_private(public, probed["verification_token"])
        status, _reused = self.request("POST", "/api/ai/connections", {
            "verification_token": probed["verification_token"], "use_as_default": True,
        })
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
