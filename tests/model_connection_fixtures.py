"""Verified model-library boundaries using synthetic books and loopback HTTP."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
from http.server import BaseHTTPRequestHandler
import io
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_backups as backups
import moyu_model_connections as connections
import moyu_settings as settings
from moyu_provider import call_chat_completion, request_messages
import server


FIRST_KEY = "synthetic-library-first-private-value"
SECOND_KEY = "synthetic-library-second-private-value"
CHANGED_KEY = "synthetic-library-changed-private-value"
PROJECT = "model-library-fixture"
BRANCH = "model-library-main"
ACTOR = "model-library-author"
STAMP = "2026-10-17T00:00:00+00:00"


class Provider(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.endswith("/models"):
            self.server.model_gets.append({
                "path": self.path, "authorization": self.headers.get("Authorization")})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(json.dumps({"data": [
                {"id": "first-model"}, {"id": "second-model"}, {"id": "first-model"}]}).encode())
            return
        self.server.unexpected_gets.append(self.path)
        self.send_error(405)

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        case = self.path.strip("/").split("/")[0]
        request_id = self.headers.get("X-Client-Request-Id")
        with sqlite3.connect(self.server.database, timeout=5) as conn:
            ledger = conn.execute(
                "SELECT q.status FROM ai_run a JOIN app_quota_reservation q "
                "ON q.ai_run_id=a.id WHERE a.id=?", (request_id,),
            ).fetchone()
        with self.server.calls_lock:
            self.server.calls.append({
                "case": case, "path": self.path, "payload": payload, "request_id": request_id,
                "authorization": self.headers.get("Authorization"),
                "ledger": ledger[0] if ledger else None,
            })
        if case == "held":
            self.server.started.set()
            self.server.release.wait(timeout=5)
        self.send_response(401 if case == "unauthorized" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if case == "unauthorized":
            body = {"error": {"message": "synthetic provider rejected " + SECOND_KEY}}
        else:
            message = {"content": "连接正常。" if case == "extra" else "连接正常"}
            if case == "reasoning":
                message["reasoning"] = "synthetic private reasoning"
            body = {
                "model": "unrequested-model" if case == "mismatch" else payload["model"],
                "choices": [{"message": message,
                             "finish_reason": "length" if case == "truncated" else "stop"}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20},
            }
            if case == "unreported":
                body.pop("model")
            if self.path.endswith("/responses"):
                body = {
                    **({"model": body["model"]} if "model" in body else {}),
                    "id": "resp-library-fixture", "object": "response", "status": "completed",
                    "output": [{"id": "msg-library-fixture", "type": "message", "role": "assistant",
                                "status": "completed", "content": [
                                    {"type": "output_text", "text": message["content"], "annotations": []}]}],
                    "usage": {"input_tokens": 13, "output_tokens": 7, "total_tokens": 20},
                }
        try:
            self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args: object) -> None:
        return


class QuietApp(server.Handler):
    def log_message(self, *_args: object) -> None:
        return


class ModelConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from http.server import ThreadingHTTPServer

        cls.providers = []
        for _ in range(2):
            provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
            provider.daemon_threads = True
            provider.calls = []
            provider.calls_lock = threading.Lock()
            provider.unexpected_gets = []
            provider.model_gets = []
            provider.started = threading.Event()
            provider.release = threading.Event()
            thread = threading.Thread(target=provider.serve_forever, daemon=True)
            thread.start()
            cls.providers.append((provider, thread))
        cls.bases = [f"http://127.0.0.1:{provider.server_port}" for provider, _ in cls.providers]

    @classmethod
    def tearDownClass(cls) -> None:
        for provider, thread in cls.providers:
            provider.release.set()
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=3)

    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="moyu-model-library-fixture-")))
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
        self.probe_threads = []
        self.api = None
        for provider, _ in self.providers:
            provider.database = self.database
            provider.calls.clear()
            provider.unexpected_gets.clear()
            provider.model_gets.clear()
            provider.started.clear()
            provider.release.clear()
        server.init_db()

    def tearDown(self) -> None:
        for provider, _ in self.providers:
            provider.release.set()
        for thread in self.probe_threads:
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive(), "synthetic probe did not terminate")
        if self.api is not None:
            self.api.shutdown()
            self.api.server_close()
            self.api_thread.join(timeout=3)
        for provider, _ in self.providers:
            self.assertEqual(provider.unexpected_gets, [])

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def seed_other_project(self) -> str:
        project = "model-library-other-book"
        with self.connect() as conn, server.workspace.use_scope(
                project, "model-library-other-main", "model-library-other-author"):
            server.ensure_seed(conn)
        return project

    def legacy(self, key: str = FIRST_KEY) -> dict:
        with self.connect() as conn:
            return settings.save_settings(conn, PROJECT, {
                "base_url": self.bases[0] + "/normal", "model": "first-model", "api_key": key,
                "daily_limit": 100,
                "parameters": {"temperature": 0, "max_tokens": 3000,
                               "thinking": {"type": "disabled"}},
            })

    def probe(self, index: int = 0, case: str = "normal", *,
              project: str = PROJECT, **overrides) -> dict:
        with self.connect() as conn:
            return connections.probe_connection(conn, project, {
                "name": "first connection" if index == 0 else "second connection",
                "base_url": self.bases[index] + "/" + case,
                "model": "first-model" if index == 0 else "second-model",
                "api_key": FIRST_KEY if index == 0 else SECOND_KEY,
                **overrides,
            })

    def save_probe(self, result: dict, project: str = PROJECT, **overrides) -> dict:
        with self.connect() as conn:
            return connections.save_connection(conn, project, {
                "verification_token": result["verification_token"], **overrides,
            })

    def saved(self, index: int = 0, *, project: str = PROJECT, **overrides) -> dict:
        return self.save_probe(self.probe(index, project=project, **overrides), project)["connection"]

    def assign(self, default: str = "", tasks: dict | None = None, **overrides) -> dict:
        with self.connect() as conn:
            return settings.save_settings(conn, PROJECT, {
                "default_connection_id": default, "task_connections": tasks or {}, **overrides,
            })

    def payload(self, project: str = PROJECT) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT settings_json FROM app_model_settings WHERE project_id=?",
                               (project,)).fetchone()
        return json.loads(row[0]) if row else {}

    def active(self, task: str = "continue") -> dict:
        with self.connect() as conn:
            return settings.task_provider_settings(settings.provider_settings(conn, PROJECT), task)

    def assert_private(self, value, *, tokens=()) -> None:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self.assertFalse(any(secret in text for secret in
                             (FIRST_KEY, SECOND_KEY, CHANGED_KEY, *tokens)),
                         "plaintext synthetic credential or verification token escaped its boundary")

    def request(self, method: str, path: str, body=None) -> tuple[int, dict]:
        if self.api is None:
            from http.server import ThreadingHTTPServer

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

    def historical_running_job(self) -> dict:
        with self.connect() as conn:
            context, _ = server.ai_context(conn, server.chapter_rows(conn)[0]["id"])
            job = server.create_ai_job(conn, "continue", context, "", "text")
            run = conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                               (job["ai_run_id"],)).fetchone()
            consent = json.loads(run[0])
            consent.pop("provider_config", None)
            conn.execute("UPDATE ai_run SET status='running',consent_json=? WHERE id=?",
                         (json.dumps(consent), job["ai_run_id"]))
            conn.execute("UPDATE app_ai_job SET status='running' WHERE id=?", (job["id"],))
        return job

    def assert_historical_running_stays_unknown(self, job: dict) -> None:
        calls_before = sum(len(provider.calls) for provider, _ in self.providers)
        for _ in range(2):
            ready = server.recover_ai_jobs()
            self.assertNotIn(job["id"], ready)
            with self.connect() as conn:
                run = conn.execute("SELECT status,error FROM ai_run WHERE id=?",
                                   (job["ai_run_id"],)).fetchone()
                saved_job = conn.execute("SELECT status FROM app_ai_job WHERE id=?",
                                         (job["id"],)).fetchone()
                reservation = conn.execute(
                    "SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                    (job["ai_run_id"],)).fetchone()
            self.assertEqual(saved_job["status"], "failed")
            self.assertEqual(run["status"], "unknown")
            self.assertTrue(run["error"])
            self.assertIsNotNone(reservation)
            self.assertEqual(reservation["status"], "unknown")
        self.worker.assert_not_called()
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), calls_before)

    def test_save_requires_a_successful_probe_receipt(self) -> None:
        for data in ({}, {"name": "unverified"},
                     {"verification_token": "unissued-synthetic-receipt-" + "x" * 24},
                     {"base_url": self.bases[0], "model": "first-model", "api_key": FIRST_KEY}):
            with self.subTest(fields=sorted(data)), self.connect() as conn:
                with self.assertRaises(ValueError):
                    connections.save_connection(conn, PROJECT, data)
        with self.connect() as conn:
            self.assertEqual(connections.public_connections(conn, PROJECT)["connections"], [])
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), 0)

    def test_remote_without_key_is_rejected_before_dispatch(self) -> None:
        with self.connect() as conn, self.assertRaises(ValueError):
            connections.probe_connection(conn, PROJECT, {
                "base_url": "https://synthetic-provider.invalid/v1", "model": "first-model",
            })
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), 0)

    def test_failed_and_mismatched_probes_never_add_or_replace_a_connection(self) -> None:
        self.legacy()
        before = self.active()
        for case in ("unauthorized", "mismatch", "truncated", "extra", "reasoning"):
            with self.subTest(case=case):
                result = self.probe(1, case)
                self.assertFalse(result["probe"]["success"])
                self.assertNotIn("verification_token", result)
                self.assertEqual(self.payload().get("connections", []), [])
                self.assertEqual(self.active(), before)
                self.assert_private(result)
        calls = self.providers[1][0].calls
        self.assertTrue(all(call["ledger"] == "reserved" for call in calls))

    def test_unreported_model_can_be_saved_and_assigned_with_identity_warning(self) -> None:
        result = self.probe(case="unreported")
        self.assertTrue(result["probe"]["success"])
        self.assertEqual(result["probe"]["identity_status"], "unreported")
        self.assertIsNone(result["probe"]["model_identity_match"])
        self.assertNotIn("error_code", result["probe"])
        connection = self.save_probe(result)["connection"]
        self.assertTrue(connection["verified"])
        self.assertEqual(connection["last_probe"]["identity_status"], "unreported")
        self.assign(connection["id"])
        self.assertEqual(self.active()["connection_id"], connection["id"])

    def test_connection_options_are_frozen_and_used_by_generation(self) -> None:
        first = self.saved()
        second = self.saved(1, output_token_parameter="max_completion_tokens", include_usage=True)
        self.assign(first["id"], {"polish": second["id"]})
        self.assertEqual(first["output_token_parameter"], "max_tokens")
        self.assertFalse(first["include_usage"])
        self.assertEqual(second["output_token_parameter"], "max_completion_tokens")
        self.assertTrue(second["include_usage"])
        with self.connect() as conn:
            config = self.active("polish")
            frozen = settings.frozen_provider_settings(
                conn, PROJECT, {name: value for name, value in config.items() if name != "api_key"})
        self.assertEqual(frozen["output_token_parameter"], "max_completion_tokens")
        self.assertTrue(frozen["include_usage"])
        result = call_chat_completion(
            frozen, [{"role": "user", "content": "synthetic generation"}],
            task="polish", max_tokens=121, stream=True)
        self.assertTrue(result["safe_to_apply"])
        payload = self.providers[1][0].calls[-1]["payload"]
        self.assertEqual(payload["max_completion_tokens"], 121)
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertEqual(self.active("continue")["output_token_parameter"], "max_tokens")
        self.assertFalse(self.active("continue")["include_usage"])

    def test_legacy_default_signature_is_accepted_but_options_cannot_be_tampered(self) -> None:
        first = self.saved()
        with self.connect() as conn:
            payload = settings._saved(conn, PROJECT)
            entry = payload["connections"][0]
            original = {
                "base_url": entry["base_url"], "model": entry["model"],
                "credential_fingerprint": hashlib.sha256(FIRST_KEY.encode()).hexdigest(),
            }
            entry["_verified_signature"] = hashlib.sha256(json.dumps(
                original, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":")).encode()).hexdigest()
            entry.pop("output_token_parameter")
            entry.pop("include_usage")
            settings._write_settings(conn, PROJECT, payload)
            public = connections.public_connections(conn, PROJECT)
            self.assertTrue(public["connections"][0]["verified"])
        self.assign(first["id"])
        self.assertEqual(self.active()["model"], "first-model")
        with self.connect() as conn:
            payload = settings._saved(conn, PROJECT)
            payload["connections"][0]["include_usage"] = True
            settings._write_settings(conn, PROJECT, payload)
            self.assertFalse(connections.public_connections(conn, PROJECT)["connections"][0]["verified"])
        with self.assertRaises(ValueError):
            self.active()

    def test_receipt_options_cannot_be_changed_between_probe_and_save(self) -> None:
        for field, replacement in (("output_token_parameter", "max_tokens"), ("include_usage", False),
                                   ("api_protocol", "responses")):
            with self.subTest(field=field):
                result = self.probe(output_token_parameter="max_completion_tokens", include_usage=True)
                with self.connect() as conn:
                    payload = settings._saved(conn, PROJECT)
                    payload["_pending_connection_probes"][-1][field] = replacement
                    settings._write_settings(conn, PROJECT, payload)
                with self.assertRaises(ValueError):
                    self.save_probe(result)

    def test_legacy_default_pending_receipt_remains_usable_after_upgrade(self) -> None:
        self.legacy()
        result = self.probe(id="legacy", api_key=None)
        with self.connect() as conn:
            payload = settings._saved(conn, PROJECT)
            receipt = payload["_pending_connection_probes"][-1]
            fingerprint = hashlib.sha256(FIRST_KEY.encode()).hexdigest()
            for field, source in (("_verified_signature", receipt), ("legacy_revision", payload)):
                original = {
                    "base_url": source["base_url"], "model": source["model"],
                    "credential_fingerprint": fingerprint,
                }
                receipt[field] = hashlib.sha256(json.dumps(
                    original, sort_keys=True, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":")).encode()).hexdigest()
            receipt.pop("output_token_parameter")
            receipt.pop("include_usage")
            settings._write_settings(conn, PROJECT, payload)
        connection = self.save_probe(result)["connection"]
        self.assertTrue(connection["verified"])
        self.assertEqual(connection["output_token_parameter"], "max_tokens")
        self.assertFalse(connection["include_usage"])

    def test_invalid_compatibility_options_fail_before_dispatch(self) -> None:
        for invalid in ({"output_token_parameter": "other"}, {"output_token_parameter": []},
                        {"include_usage": 1}, {"include_usage": None},
                        {"api_protocol": "other"}, {"api_protocol": []}, {"api_protocol": None}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.probe(**invalid)
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), 0)

    def test_previous_two_option_signatures_keep_chat_but_never_accept_a_protocol_change(self) -> None:
        connection = self.saved(output_token_parameter="max_completion_tokens", include_usage=True)
        with self.connect() as conn:
            payload = settings._saved(conn, PROJECT)
            entry = payload["connections"][0]
            old_fields = {
                "base_url": entry["base_url"], "model": entry["model"],
                "credential_fingerprint": hashlib.sha256(FIRST_KEY.encode()).hexdigest(),
                "output_token_parameter": "max_completion_tokens", "include_usage": True,
            }
            entry["_verified_signature"] = hashlib.sha256(json.dumps(
                old_fields, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":")).encode()).hexdigest()
            entry.pop("api_protocol")
            settings._write_settings(conn, PROJECT, payload)
            self.assertTrue(connections.public_connections(conn, PROJECT)["connections"][0]["verified"])
        self.assign(connection["id"])
        self.assertEqual(self.active()["api_protocol"], "chat_completions")
        with self.connect() as conn:
            payload = settings._saved(conn, PROJECT)
            payload["connections"][0]["api_protocol"] = "responses"
            settings._write_settings(conn, PROJECT, payload)
            self.assertFalse(connections.public_connections(conn, PROJECT)["connections"][0]["verified"])
        with self.assertRaises(ValueError):
            self.active()

    def test_previous_two_option_receipt_can_be_saved_but_not_reinterpreted_as_responses(self) -> None:
        result = self.probe(output_token_parameter="max_completion_tokens", include_usage=True)
        with self.connect() as conn:
            payload = settings._saved(conn, PROJECT)
            receipt = payload["_pending_connection_probes"][-1]
            fields = {
                "base_url": receipt["base_url"], "model": receipt["model"],
                "credential_fingerprint": hashlib.sha256(FIRST_KEY.encode()).hexdigest(),
                "output_token_parameter": "max_completion_tokens", "include_usage": True,
            }
            receipt["_verified_signature"] = hashlib.sha256(json.dumps(
                fields, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":")).encode()).hexdigest()
            receipt.pop("api_protocol")
            settings._write_settings(conn, PROJECT, payload)
        connection = self.save_probe(result)["connection"]
        self.assertTrue(connection["verified"])
        self.assertEqual(connection["api_protocol"], "chat_completions")
        self.assertEqual(connection["output_token_parameter"], "max_completion_tokens")
        self.assertTrue(connection["include_usage"])

    def test_responses_connection_reaches_generation_and_retains_identity_checks(self) -> None:
        second = self.saved(1, api_protocol="responses", include_usage=True)
        self.assertEqual(second["api_protocol"], "responses")
        self.assertEqual(self.providers[1][0].calls[-1]["path"], "/normal/responses")
        self.assign(second["id"])
        config = self.active()
        generated = call_chat_completion(
            config, [{"role": "user", "content": "synthetic response generation"}],
            task="continue", max_tokens=128, stream=True)
        self.assertTrue(generated["safe_to_apply"])
        payload = self.providers[1][0].calls[-1]["payload"]
        self.assertEqual(payload["max_output_tokens"], 128)
        self.assertIn("input", payload)
        self.assertNotIn("messages", payload)
        self.assertNotIn("stream_options", payload)
        rejected = self.probe(case="mismatch", api_protocol="responses")
        self.assertFalse(rejected["probe"]["success"])
        self.assertNotIn("verification_token", rejected)
        unreported = self.probe(case="unreported", api_protocol="responses")
        self.assertTrue(unreported["probe"]["success"])
        self.assertEqual(unreported["probe"]["identity_status"], "unreported")

    def test_model_inventory_inherits_saved_protocol_and_allows_an_explicit_draft_override(self) -> None:
        connection = self.saved(api_protocol="responses")
        before = self.payload()
        with self.connect() as conn, patch.object(
                connections.moyu_provider, "list_models",
                return_value={"models": ["fixture"], "endpoint": "synthetic-endpoint"}) as listing:
            connections.list_connection_models(conn, PROJECT, {
                "id": connection["id"], "base_url": self.bases[0] + "/normal",
            })
            self.assertEqual(listing.call_args.args[0]["api_protocol"], "responses")
            connections.list_connection_models(conn, PROJECT, {
                "id": connection["id"], "base_url": self.bases[0] + "/normal",
                "api_protocol": "chat_completions",
            })
            self.assertEqual(listing.call_args.args[0]["api_protocol"], "chat_completions")
        self.assertEqual(self.payload(), before)

    def test_assignment_checks_effective_response_parameters_and_rolls_back_invalid_settings(self) -> None:
        connection = self.saved(api_protocol="responses")
        before = self.payload()
        calls_before = sum(len(provider.calls) for provider, _ in self.providers)
        for parameters in (
            {"temperature": 0, "max_tokens": 8},
            {"temperature": 0, "max_tokens": 3000, "thinking": {"type": "disabled"}},
            {"temperature": 0, "max_tokens": 3000, "seed": 0},
            {"temperature": 0, "max_tokens": 3000, "stop": ["stop"]},
            {"temperature": 0, "max_tokens": 3000, "presence_penalty": 0},
            {"temperature": 0, "max_tokens": 3000, "frequency_penalty": 0},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.assign(connection["id"], parameters=parameters)
            self.assertEqual(self.payload(), before)
        saved = self.assign(connection["id"], parameters={
            "temperature": 0, "max_tokens": 16, "reasoning_effort": "none"})
        self.assertEqual(saved["default_connection_id"], connection["id"])
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), calls_before)

    def test_response_connection_probe_ignores_chat_only_writing_parameters_but_assignment_checks_them(self) -> None:
        self.legacy()
        parameters = {"temperature": 0, "max_tokens": 3000, "stop": ["END"],
                      "thinking": {"type": "disabled"}}
        with self.connect() as conn:
            settings.save_settings(conn, PROJECT, {"parameters": parameters})
        calls_before = sum(len(provider.calls) for provider, _ in self.providers)
        connection = self.saved(1, api_protocol="responses")
        payload = self.providers[1][0].calls[-1]["payload"]
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["max_output_tokens"], settings.PROBE_MAX_TOKENS)
        self.assertNotIn("thinking", payload)
        self.assertNotIn("stop", payload)
        self.assertNotIn("reasoning", payload)
        self.assertTrue(connection["verified"])
        before = self.payload()
        with self.assertRaises(ValueError):
            self.assign(connection["id"])
        self.assertEqual(self.payload(), before)
        saved = self.assign(connection["id"], parameters={
            "temperature": 0, "max_tokens": 3000, "reasoning_effort": "none"})
        self.assertEqual(saved["default_connection_id"], connection["id"])
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), calls_before + 1)

    def test_task_assignment_validates_its_overrides_and_parent_alias_protocol(self) -> None:
        chat, responses = self.saved(), self.saved(1, api_protocol="responses")
        shared = {"temperature": 0, "max_tokens": 8, "thinking": {"type": "disabled"}}
        saved = self.assign(
            chat["id"], {"summary": responses["id"]}, parameters=shared,
            task_parameters={"summary": {"max_tokens": 32, "reasoning_effort": "none"}})
        self.assertEqual(saved["task_connections"], {"summary": responses["id"]})
        self.assertEqual(self.active("chapter_analysis")["api_protocol"], "responses")
        before = self.payload()
        for task_parameters in (
            {"summary": {"max_tokens": 32}},
            {"summary": {"max_tokens": 8, "reasoning_effort": "none"}},
            {"summary": {"max_tokens": 32, "reasoning_effort": "none"},
             "chapter_analysis": {"max_tokens": 8, "reasoning_effort": "none"}},
        ):
            with self.subTest(task_parameters=task_parameters), self.assertRaises(ValueError):
                self.assign(chat["id"], {"summary": responses["id"]},
                            parameters=shared, task_parameters=task_parameters)
            self.assertEqual(self.payload(), before)

    def test_assignment_uses_chosen_protocol_instead_of_obsolete_legacy_protocol(self) -> None:
        with self.connect() as conn:
            settings.save_settings(conn, PROJECT, {
                "base_url": self.bases[0] + "/normal", "model": "first-model", "api_key": FIRST_KEY,
                "api_protocol": "responses", "parameters": {"temperature": 0, "max_tokens": 3000}})
        connection = self.saved()
        saved = self.assign(connection["id"], parameters={
            "temperature": 0, "max_tokens": 8, "thinking": {"type": "disabled"}})
        self.assertEqual(saved["default_connection_id"], connection["id"])
        self.assertEqual(self.active()["api_protocol"], "chat_completions")

    def test_model_inventory_uses_unsaved_form_without_model_or_side_effects(self) -> None:
        with self.connect() as conn:
            before = "\n".join(conn.iterdump())
            result = connections.list_connection_models(conn, PROJECT, {
                "base_url": self.bases[0] + "/inventory/v1", "api_key": FIRST_KEY,
            })
            self.assertEqual("\n".join(conn.iterdump()), before)
            self.assertEqual(conn.total_changes, 0)
        self.assertEqual(set(result["models"]), {"first-model", "second-model"})
        self.assertEqual(result["endpoint"], self.bases[0] + "/inventory/v1/models")
        self.assertTrue(self.providers[0][0].model_gets[-1]["authorization"] == "Bearer " + FIRST_KEY)
        self.assert_private(result)
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), 0)
        self.assertFalse((self.root / "credentials").exists())

    def test_model_inventory_saved_and_legacy_keys_are_project_scoped_and_overridable(self) -> None:
        self.legacy()
        first = self.saved()
        before = self.payload()
        credentials = set((self.root / "credentials").iterdir())
        for identifier in ("legacy", first["id"]):
            with self.subTest(identifier=identifier), self.connect() as conn:
                for suffix in ("", "/", "/chat/completions", "/responses"):
                    result = connections.list_connection_models(conn, PROJECT, {
                        "id": identifier, "base_url": self.bases[0] + "/normal" + suffix})
                    self.assertEqual(result["endpoint"], self.bases[0] + "/normal/models")
                    self.assertTrue(
                        self.providers[0][0].model_gets[-1]["authorization"] == "Bearer " + FIRST_KEY)
                for changed_base in (
                    self.bases[1] + "/normal", self.bases[0] + "/inventory/v1",
                    self.bases[0].replace("127.0.0.1", "localhost") + "/normal",
                ):
                    gets_before = sum(len(provider.model_gets) for provider, _ in self.providers)
                    with self.assertRaisesRegex(ValueError, "服务地址已改变"):
                        connections.list_connection_models(conn, PROJECT, {
                            "id": identifier, "base_url": changed_base})
                    self.assertEqual(
                        sum(len(provider.model_gets) for provider, _ in self.providers), gets_before)
                override = connections.list_connection_models(conn, PROJECT, {
                    "id": identifier, "base_url": self.bases[1] + "/inventory/v1",
                    "api_key": SECOND_KEY,
                })
                self.assertTrue(self.providers[1][0].model_gets[-1]["authorization"] == "Bearer " + SECOND_KEY)
                self.assert_private(override)
                connections.list_connection_models(conn, PROJECT, {
                    "id": identifier, "base_url": self.bases[1] + "/inventory/v1", "api_key": ""})
                self.assertIsNone(self.providers[1][0].model_gets[-1]["authorization"])
        self.assertEqual(self.payload(), before)
        self.assertEqual(set((self.root / "credentials").iterdir()), credentials)
        other = self.seed_other_project()
        with self.connect() as conn, self.assertRaises(ValueError):
            connections.list_connection_models(conn, other, {
                "id": first["id"], "base_url": self.bases[0] + "/inventory/v1"})

    def test_saved_key_service_matching_normalizes_host_port_and_endpoint_suffixes(self) -> None:
        for base_url in (
            "https://example.invalid/v1", "https://EXAMPLE.invalid:443/v1/",
            "https://example.invalid/v1/chat/completions",
            "https://example.invalid/v1/responses/",
        ):
            with self.subTest(base_url=base_url):
                settings._validate_saved_key_target(base_url, "https://example.invalid:443/v1")
        for base_url in (
            "https://example.invalid:444/v1", "https://elsewhere.invalid/v1",
            "https://example.invalid/v2", "http://localhost/v1",
        ):
            with self.subTest(base_url=base_url), self.assertRaisesRegex(ValueError, "服务地址已改变"):
                settings._validate_saved_key_target(base_url, "https://example.invalid/v1")

    def test_probe_cannot_reuse_saved_key_for_a_different_service_without_explicit_key(self) -> None:
        self.legacy()
        connection = self.saved()
        for identifier in ("legacy", connection["id"]):
            with self.subTest(identifier=identifier):
                before = self.payload()
                calls_before = sum(len(provider.calls) for provider, _ in self.providers)
                with self.assertRaisesRegex(ValueError, "服务地址已改变"):
                    self.probe(1, id=identifier, api_key=None)
                self.assertEqual(self.payload(), before)
                self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), calls_before)
                result = self.probe(
                    0, id=identifier, api_key=None,
                    base_url=self.bases[0] + "/normal/chat/completions")
                self.assertTrue(result["probe"]["success"])
                self.assertTrue(
                    self.providers[0][0].calls[-1]["authorization"] == "Bearer " + FIRST_KEY)
                result = self.probe(1, id=identifier, api_key="")
                self.assertTrue(result["probe"]["success"])
                self.assertIsNone(self.providers[1][0].calls[-1]["authorization"])
                result = self.probe(1, id=identifier, api_key=SECOND_KEY)
                self.assertTrue(result["probe"]["success"])
                self.assertTrue(
                    self.providers[1][0].calls[-1]["authorization"] == "Bearer " + SECOND_KEY)

    def test_model_inventory_rejects_unknown_fields_missing_credentials_and_invalid_ids(self) -> None:
        for data in (
            {"base_url": self.bases[0], "model": "unused"},
            {"base_url": self.bases[0], "parameters": {}},
            {"base_url": "https://synthetic-provider.invalid/v1"},
            {"base_url": self.bases[0], "id": False},
            {"base_url": self.bases[0], "id": "missing-connection"},
            {"base_url": self.bases[0], "api_key": []},
        ):
            with self.subTest(data=data), self.connect() as conn, self.assertRaises(ValueError):
                connections.list_connection_models(conn, PROJECT, data)
        self.assertTrue(all(not provider.model_gets for provider, _ in self.providers))

    def test_model_inventory_http_route_preserves_connection_settings(self) -> None:
        before = self.payload()
        status, result = self.request("POST", "/api/ai/connections/models", {
            "base_url": self.bases[0] + "/inventory/v1", "api_key": FIRST_KEY})
        self.assertEqual(status, 200)
        self.assertEqual(set(result["models"]), {"first-model", "second-model"})
        self.assertEqual(self.payload(), before)
        self.assert_private(result)

    def test_probe_and_library_save_preserve_legacy_until_explicit_assignment(self) -> None:
        self.legacy()
        before = self.active()
        result = self.probe(1)
        self.assertTrue(result["probe"]["success"])
        self.assertEqual(self.active(), before)
        connection = self.save_probe(result)["connection"]
        self.assertEqual(self.active(), before)
        with self.connect() as conn:
            public = settings.public_settings(conn, PROJECT)
        self.assertFalse(public["connection_workflow"])
        self.assertEqual(public["model"], "first-model")
        public = self.assign(connection["id"])
        self.assertTrue(public["connection_workflow"])
        self.assertEqual(self.active()["model"], "second-model")
        self.assertTrue(self.active()["api_key"] == SECOND_KEY)

    def test_saved_connection_without_assignment_does_not_enable_a_new_book(self) -> None:
        connection = self.saved()
        with self.connect() as conn:
            public = settings.public_settings(conn, PROJECT)
        self.assertTrue(connection["verified"])
        self.assertFalse(public["configured"])
        self.assertEqual(public["default_connection_id"], "")
        self.assign(connection["id"])
        self.assertEqual(self.active()["connection_id"], connection["id"])

    def test_receipt_is_one_use(self) -> None:
        result = self.probe()
        self.save_probe(result)
        before = self.payload()
        with self.assertRaises(ValueError):
            self.save_probe(result)
        self.assertEqual(self.payload(), before)
        self.assertEqual(len(self.payload()["connections"]), 1)

    def test_receipt_expires_at_thirty_minutes(self) -> None:
        with patch.object(connections, "_now", return_value=STAMP):
            result = self.probe()
        pending = self.payload()["_pending_connection_probes"][0]
        self.assertEqual(pending["expires_at"], "2026-10-17T00:30:00+00:00")
        with patch.object(connections, "_now", return_value="2026-10-17T00:30:00+00:00"):
            with self.assertRaises(ValueError):
                self.save_probe(result)
        self.assertEqual(self.payload().get("connections", []), [])

    def test_receipt_remains_usable_before_expiry(self) -> None:
        with patch.object(connections, "_now", return_value=STAMP):
            result = self.probe()
        with patch.object(connections, "_now", return_value="2026-10-17T00:29:59+00:00"):
            connection = self.save_probe(result)["connection"]
        self.assertTrue(connection["verified"])

    def test_receipt_cannot_cross_projects_even_when_copied(self) -> None:
        result = self.probe()
        other = self.seed_other_project()
        with self.assertRaises(ValueError):
            self.save_probe(result, other)
        copied = self.payload()["_pending_connection_probes"]
        with self.connect() as conn:
            payload = settings._environment()
            payload["_pending_connection_probes"] = copy.deepcopy(copied)
            settings._write_settings(conn, other, payload)
        with self.assertRaises(ValueError):
            self.save_probe(result, other)
        self.assertEqual(self.payload(other).get("connections", []), [])
        self.assertTrue(self.save_probe(result)["connection"]["verified"])

    def test_receipt_cannot_be_used_from_another_database(self) -> None:
        result = self.probe()
        payload = self.payload()
        with patch.object(server, "DB_PATH", self.root / "other.sqlite3"):
            server.init_db()
            with server.connect() as conn:
                settings._write_settings(conn, PROJECT, payload)
            with server.connect() as conn, self.assertRaises(ValueError):
                connections.save_connection(conn, PROJECT, {
                    "verification_token": result["verification_token"],
                })
        self.assertTrue(self.save_probe(result)["connection"]["verified"])

    def test_old_revision_receipt_cannot_overwrite_a_newer_saved_edit(self) -> None:
        original = self.saved()
        old = self.probe(1, id=original["id"], model="older-edit")
        newer = self.probe(1, id=original["id"], model="newer-edit")
        latest = self.save_probe(newer)["connection"]
        self.assertEqual(latest["revision"], original["revision"] + 1)
        with self.assertRaises(ValueError):
            self.save_probe(old)
        self.assertEqual(self.payload()["connections"][0]["model"], "newer-edit")

    def test_receipt_freezes_tuple_and_rejects_save_time_provider_overrides(self) -> None:
        result = self.probe()
        for extra in ({"base_url": self.bases[1]}, {"model": "different-model"},
                      {"api_key": CHANGED_KEY}, {"id": "different-id"}):
            with self.subTest(field=next(iter(extra))), self.assertRaises(ValueError):
                self.save_probe(result, **extra)
        connection = self.save_probe(result, name="renamed after testing")["connection"]
        self.assign(connection["id"])
        self.assertEqual(connection["name"], "renamed after testing")
        self.assertEqual(self.active()["base_url"], self.bases[0] + "/normal")
        self.assertEqual(self.active()["model"], "first-model")
        self.assertTrue(self.active()["api_key"] == FIRST_KEY)

    def test_missing_or_changed_private_key_invalidates_pending_receipt(self) -> None:
        for change in ("replace", "remove"):
            with self.subTest(change=change):
                result = self.probe()
                reference = self.payload()["_pending_connection_probes"][-1]["_credential_ref"]
                path = self.root / "credentials" / (reference + ".json")
                if change == "replace":
                    path.write_text(json.dumps({"api_key": CHANGED_KEY}), encoding="utf-8")
                else:
                    path.unlink()
                with self.assertRaises(ValueError):
                    self.save_probe(result)
        self.assertEqual(self.payload().get("connections", []), [])

    def test_failed_edit_with_new_key_keeps_verified_connection_and_active_key(self) -> None:
        connection = self.saved()
        self.assign(connection["id"])
        before = self.active()
        public_before = self.payload()["connections"]
        result = self.probe(1, "unauthorized", id=connection["id"], api_key=CHANGED_KEY)
        self.assertFalse(result["probe"]["success"])
        self.assertNotIn("verification_token", result)
        self.assertEqual(self.active(), before)
        self.assertEqual(self.payload()["connections"], public_before)
        self.assert_private(result)

    def test_edit_saved_during_inflight_probe_invalidates_old_probe_result(self) -> None:
        original = self.saved()
        self.assign(original["id"])
        outcome = []
        errors = []

        def held_probe() -> None:
            try:
                outcome.append(self.probe(1, "held", id=original["id"], api_key=CHANGED_KEY))
            except Exception as error:
                errors.append(type(error).__name__)

        thread = threading.Thread(target=held_probe)
        self.probe_threads.append(thread)
        thread.start()
        provider = self.providers[1][0]
        self.assertTrue(provider.started.wait(timeout=3), "synthetic provider was not reached")
        winner = self.save_probe(self.probe(0, id=original["id"], model="winning-edit"))
        provider.release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(outcome), 1)
        self.assertFalse(outcome[0]["probe"]["success"])
        self.assertTrue(outcome[0]["probe"]["configuration_stale"])
        self.assertNotIn("verification_token", outcome[0])
        self.assertEqual(self.active()["model"], winner["connection"]["model"])
        self.assertTrue(self.active()["api_key"] == FIRST_KEY)

    def test_unknown_foreign_or_unverified_connections_cannot_be_assigned(self) -> None:
        first = self.saved()
        other = self.seed_other_project()
        foreign = self.saved(1, project=other)
        pending = self.probe(1)
        for data in (
            {"default_connection_id": "missing-connection"},
            {"default_connection_id": foreign["id"]},
            {"default_connection_id": pending["verification_token"]},
            {"default_connection_id": first["id"], "task_connections": {"unknown": first["id"]}},
            {"default_connection_id": first["id"], "task_connections": {"polish": foreign["id"]}},
        ):
            before = self.payload()
            with self.subTest(fields=sorted(data)), self.connect() as conn:
                with self.assertRaises(ValueError):
                    settings.save_settings(conn, PROJECT, data)
            self.assertEqual(self.payload(), before)
        with self.connect() as conn:
            payload = settings._saved(conn, PROJECT)
            payload["connections"][0]["last_probe"] = None
            settings._write_settings(conn, PROJECT, payload)
        with self.assertRaises(ValueError):
            self.assign(first["id"])

    def test_raw_tuple_and_legacy_task_models_cannot_bypass_library_workflow(self) -> None:
        connection = self.saved()
        self.assign(connection["id"])
        for data in (
            {"base_url": self.bases[1]}, {"model": "untested-model"}, {"api_key": SECOND_KEY},
            {"models": {"polish": "untested-model"}}, {"clear_api_key": True},
        ):
            before = self.payload()
            with self.subTest(field=next(iter(data))), self.connect() as conn:
                with self.assertRaises(ValueError):
                    settings.save_settings(conn, PROJECT, data)
            self.assertEqual(self.payload(), before)

    def test_two_providers_receive_correct_url_model_key_parameters_and_aliases(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {"polish": second["id"], "summary": second["id"],
                                 "check": second["id"]},
                    parameters={"temperature": 0.2, "max_tokens": 1200},
                    task_parameters={"polish": {"temperature": 0.9, "max_tokens": 600},
                                     "summary": {"temperature": 0, "max_tokens": 500}},
                    styles={"tone": "restrained", "instructions": "preserve known facts"})
        with self.connect() as conn:
            config = settings.provider_settings(conn, PROJECT)
        self.assert_private(config["_connection_routes"])
        self.assertNotIn("api_key", json.dumps(config["_connection_routes"]))
        for task, expected_index in (
            ("continue", 0), ("polish", 1), ("summary", 1), ("chapter_analysis", 1),
            ("check", 1), ("semantic_review", 1),
        ):
            with self.subTest(task=task):
                resolved = settings.task_provider_settings(config, task)
                self.assertNotIn("_connection_routes", resolved)
                self.assertEqual(resolved["base_url"], self.bases[expected_index] + "/normal")
                self.assertEqual(resolved["model"], "first-model" if expected_index == 0 else "second-model")
                self.assertTrue(resolved["api_key"] == (FIRST_KEY if expected_index == 0 else SECOND_KEY))
                result = call_chat_completion(
                    resolved, [{"role": "user", "content": "synthetic request"}],
                    task=task, max_tokens=32)
                self.assertTrue(result["safe_to_apply"])
                call = self.providers[expected_index][0].calls[-1]
                self.assertTrue(call["authorization"] == "Bearer " +
                                (FIRST_KEY if expected_index == 0 else SECOND_KEY))
                self.assertEqual(call["payload"]["model"], resolved["model"])
                if task == "polish":
                    self.assertEqual(call["payload"]["temperature"], 0.9)
                if task == "chapter_analysis":
                    self.assertEqual(call["payload"]["temperature"], 0)
        public = self.payload()
        self.assertEqual(public["task_connections"]["summary"], second["id"])
        self.assertEqual(public["task_connections"]["check"], second["id"])
        self.assertNotIn("chapter_analysis", public["task_connections"])
        self.assertNotIn("semantic_review", public["task_connections"])

    def test_summary_and_check_do_not_overwrite_explicit_analysis_connections(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {
            "summary": first["id"], "chapter_analysis": second["id"],
            "check": second["id"], "semantic_review": first["id"],
        }, task_parameters={
            "summary": {"temperature": 0.1, "max_tokens": 111},
            "chapter_analysis": {"temperature": 0.2, "max_tokens": 222},
            "check": {"temperature": 0.3, "max_tokens": 333},
            "semantic_review": {"temperature": 0.4, "max_tokens": 444},
        })
        expected = {
            "summary": (0, 0.1, 111), "chapter_analysis": (1, 0.2, 222),
            "check": (1, 0.3, 333), "semantic_review": (0, 0.4, 444),
        }
        for task, (index, temperature, output_limit) in expected.items():
            with self.subTest(task=task):
                config = self.active(task)
                self.assertEqual(config["connection_id"], (first, second)[index]["id"])
                self.assertEqual(config["base_url"], self.bases[index] + "/normal")
                self.assertEqual(config["task_parameters"][task]["temperature"], temperature)
                self.assertEqual(config["task_max_tokens"][task], output_limit)
                call_chat_completion(
                    config, [{"role": "user", "content": "synthetic independent purpose"}],
                    task=task, max_tokens=output_limit)
                call = self.providers[index][0].calls[-1]
                self.assertTrue(call["authorization"] == "Bearer " + (FIRST_KEY, SECOND_KEY)[index])
                self.assertEqual(call["payload"]["max_tokens"], output_limit)
                self.assertEqual(call["payload"]["temperature"], temperature)
        assignments = self.payload()["task_connections"]
        self.assertEqual(assignments["summary"], first["id"])
        self.assertEqual(assignments["chapter_analysis"], second["id"])
        self.assertEqual(assignments["check"], second["id"])
        self.assertEqual(assignments["semantic_review"], first["id"])

    def test_parent_purposes_never_inherit_child_purpose_connections(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {"chapter_analysis": second["id"], "semantic_review": second["id"]})
        for task in ("summary", "check"):
            with self.subTest(task=task):
                self.assertEqual(self.active(task)["connection_id"], first["id"])
        for task in ("chapter_analysis", "semantic_review"):
            with self.subTest(task=task):
                self.assertEqual(self.active(task)["connection_id"], second["id"])

    def test_invalid_unrelated_connection_does_not_block_valid_purposes(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {"polish": second["id"]})
        second_entry = next(entry for entry in self.payload()["connections"] if entry["id"] == second["id"])
        (self.root / "credentials" / (second_entry["_credential_ref"] + ".json")).unlink()
        for task in ("continue", "summary", "chapter_analysis", "check", "semantic_review"):
            with self.subTest(task=task):
                self.assertEqual(self.active(task)["connection_id"], first["id"])
        with self.assertRaises(ValueError):
            self.active("polish")
        with self.connect() as conn:
            context, _ = server.ai_context(conn, server.chapter_rows(conn)[0]["id"])
            config = settings.provider_settings(conn, PROJECT)
            preview = server.ai_prompt_preview("continue", context, "", "text", config=config)
            self.assertEqual(preview["request"]["model"], "first-model")
            result = server.call_ai_provider(config, "continue", context, "", "text", max_tokens=32)
        self.assertEqual(result["returned_model"], "first-model")
        self.assertTrue(self.providers[0][0].calls[-1]["authorization"] == "Bearer " + FIRST_KEY)

    def test_invalid_default_allows_valid_override_but_explicit_bad_never_falls_back(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {"polish": second["id"], "summary": first["id"],
                                 "chapter_analysis": second["id"], "check": second["id"],
                                 "semantic_review": first["id"]})
        first_entry = next(entry for entry in self.payload()["connections"] if entry["id"] == first["id"])
        (self.root / "credentials" / (first_entry["_credential_ref"] + ".json")).unlink()
        for task in ("polish", "chapter_analysis", "check"):
            with self.subTest(task=task):
                self.assertEqual(self.active(task)["connection_id"], second["id"])
        for task in ("continue", "summary", "semantic_review"):
            with self.subTest(task=task), self.assertRaises(ValueError):
                self.active(task)
        with self.connect() as conn:
            context, _ = server.ai_context(conn, server.chapter_rows(conn)[0]["id"])
            config = settings.provider_settings(conn, PROJECT)
            preview = server.ai_prompt_preview("polish", context, "", "text", config=config)
            self.assertEqual(preview["request"]["model"], "second-model")
            result = server.call_ai_provider(config, "polish", context, "", "text", max_tokens=32)
            self.assertEqual(result["returned_model"], "second-model")
            with self.assertRaises(ValueError):
                server.ai_prompt_preview("continue", context, "", "text", config=config)
        self.assertTrue(self.providers[1][0].calls[-1]["authorization"] == "Bearer " + SECOND_KEY)

    def test_all_nine_server_tasks_freeze_only_their_selected_provider(self) -> None:
        first = self.saved()
        second = self.saved(1, output_token_parameter="max_completion_tokens", include_usage=True,
                            api_protocol="responses")
        selected = {
            "continue": first["id"], "polish": second["id"], "outline": first["id"],
            "scene": second["id"], "summary": first["id"], "check": second["id"],
            "brainstorm": first["id"], "chapter_analysis": second["id"],
            "semantic_review": first["id"],
        }
        self.assertEqual(set(selected), settings.TASKS)
        self.assign(first["id"], selected)
        calls_before = sum(len(provider.calls) for provider, _ in self.providers)
        with self.connect() as conn:
            for task, identifier in selected.items():
                with self.subTest(task=task):
                    context, _ = server.ai_context(conn, server.chapter_rows(conn)[0]["id"])
                    response_format = server.ai_contract(task)["response_format"]
                    job = server.create_ai_job(conn, task, context, "", response_format)
                    row = conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                       (job["ai_run_id"],)).fetchone()
                    consent = json.loads(row[0])
                    frozen = consent["provider_config"]
                    self.assertEqual(frozen["connection_id"], identifier)
                    self.assertNotIn("_connection_routes", frozen)
                    self.assertNotIn("api_key", frozen)
                    self.assert_private(frozen)
                    index = 0 if identifier == first["id"] else 1
                    self.assertEqual(frozen["base_url"], self.bases[index] + "/normal")
                    self.assertEqual(frozen["model"], ("first-model", "second-model")[index])
                    self.assertEqual(frozen["connection_revision"], (first, second)[index]["revision"])
                    self.assertEqual(frozen["output_token_parameter"],
                                     ("max_tokens", "max_completion_tokens")[index])
                    self.assertEqual(frozen["include_usage"], index == 1)
                    self.assertEqual(frozen["api_protocol"], ("chat_completions", "responses")[index])
                    prepared = server.prepare_frozen_ai_request(
                        task, response_format, 3000, frozen, consent["request_audit"]["payload"])
                    self.assertEqual(prepared["payload"], consent["request_audit"]["payload"])
                    self.assertEqual(prepared["messages"], request_messages(
                        prepared["payload"], frozen["api_protocol"]))
                    self.assertTrue(server.retry_provider_config(conn, frozen)["api_key"] ==
                                    (FIRST_KEY, SECOND_KEY)[index])
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), calls_before)

    def test_snapshotless_running_task_only_connection_is_never_requeued(self) -> None:
        connection = self.saved()
        self.assign(tasks={"continue": connection["id"]})
        self.assertEqual(self.payload()["default_connection_id"], "")
        job = self.historical_running_job()
        self.assert_historical_running_stays_unknown(job)

    def test_snapshotless_running_with_invalid_default_is_never_requeued(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {"continue": second["id"]})
        job = self.historical_running_job()
        reference = next(entry["_credential_ref"] for entry in self.payload()["connections"]
                         if entry["id"] == first["id"])
        (self.root / "credentials" / (reference + ".json")).unlink()
        self.assertEqual(self.active("continue")["connection_id"], second["id"])
        self.assert_historical_running_stays_unknown(job)

    def test_snapshotless_running_with_invalid_task_connection_is_never_requeued(self) -> None:
        connection = self.saved()
        self.assign(tasks={"continue": connection["id"]})
        job = self.historical_running_job()
        reference = self.payload()["connections"][0]["_credential_ref"]
        (self.root / "credentials" / (reference + ".json")).unlink()
        with self.assertRaises(ValueError):
            self.active("continue")
        self.assert_historical_running_stays_unknown(job)

    def test_snapshotless_running_legacy_remote_keeps_unknown_recovery(self) -> None:
        self.legacy()
        self.assert_historical_running_stays_unknown(self.historical_running_job())

    def test_snapshotless_running_legacy_offline_keeps_local_requeue_behavior(self) -> None:
        job = self.historical_running_job()
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), 0)
        for _ in range(2):
            ready = server.recover_ai_jobs()
            self.assertIn(job["id"], ready)
            with self.connect() as conn:
                run_status = conn.execute("SELECT status FROM ai_run WHERE id=?",
                                          (job["ai_run_id"],)).fetchone()[0]
                job_status = conn.execute("SELECT status FROM app_ai_job WHERE id=?",
                                          (job["id"],)).fetchone()[0]
            self.assertEqual(run_status, "queued")
            self.assertEqual(job_status, "queued")
        self.worker.assert_not_called()
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), 0)

    def test_changed_saved_credentials_disable_use_and_new_assignments(self) -> None:
        connection = self.saved()
        self.assign(connection["id"])
        reference = self.payload()["connections"][0]["_credential_ref"]
        path = self.root / "credentials" / (reference + ".json")
        path.write_text(json.dumps({"api_key": CHANGED_KEY}), encoding="utf-8")
        with self.connect() as conn:
            public = settings.public_settings(conn, PROJECT)
            self.assertFalse(public["connections"][0]["verified"])
            self.assertFalse(public["configured"])
        with self.assertRaises(ValueError):
            self.active()
        with self.assertRaises(ValueError):
            self.assign(connection["id"])

    def test_default_or_task_assigned_connection_cannot_be_deleted(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {"polish": second["id"]})
        for identifier in (first["id"], second["id"]):
            before = self.payload()
            with self.subTest(connection=identifier), self.connect() as conn:
                with self.assertRaises(ValueError):
                    connections.remove_connection(conn, PROJECT, identifier)
            self.assertEqual(self.payload(), before)
        self.assign(first["id"])
        with self.connect() as conn:
            result = connections.remove_connection(conn, PROJECT, second["id"])
        self.assertEqual([entry["id"] for entry in result["settings"]["connections"]], [first["id"]])
        self.assertEqual(self.active()["connection_id"], first["id"])

    def test_legacy_key_is_reusable_only_when_explicitly_selected(self) -> None:
        self.legacy()
        before = self.active()
        result = self.probe(api_key=None, id="legacy")
        self.assertTrue(result["probe"]["success"])
        connection = self.save_probe(result)["connection"]
        self.assertEqual(self.active(), before)
        self.assign(connection["id"])
        self.assertTrue(self.active()["api_key"] == FIRST_KEY)
        call = self.providers[0][0].calls[-1]
        self.assertTrue(call["authorization"] == "Bearer " + FIRST_KEY)
        result = self.probe(1, api_key=None)
        self.assertTrue(result["probe"]["success"])
        saved = self.save_probe(result)["connection"]
        self.assertFalse(saved["api_key_configured"])
        self.assertIsNone(self.providers[1][0].calls[-1]["authorization"])

    def test_public_database_backup_and_job_snapshot_exclude_plaintext_secrets(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {"continue": second["id"]})
        pending = self.probe()
        with self.connect() as conn:
            chapter = server.chapter_rows(conn)[0]
            context, _ = server.ai_context(conn, chapter["id"])
            job = server.create_ai_job(conn, "continue", context, "synthetic writing instruction", "text")
            row = conn.execute("SELECT consent_json FROM ai_run WHERE id=?", (job["ai_run_id"],)).fetchone()
            consent = json.loads(row[0])
            frozen = consent["provider_config"]
            self.assertNotIn("api_key", frozen)
            self.assertNotIn("_connection_routes", frozen)
            self.assertEqual(frozen["connection_id"], second["id"])
            self.assertEqual(frozen["base_url"], self.bases[1] + "/normal")
            self.assertEqual(frozen["model"], "second-model")
            self.assertTrue(server.retry_provider_config(conn, frozen)["api_key"] == SECOND_KEY)
            public = settings.public_settings(conn, PROJECT)
            self.assert_private(public, tokens=(pending["verification_token"],))
            raw_database = "\n".join(conn.iterdump())
            self.assert_private(raw_database, tokens=(pending["verification_token"],))
            token_hash = hashlib.sha256(pending["verification_token"].encode()).hexdigest()
            self.assertIn(token_hash, raw_database)
            payload = backups.capture(conn, PROJECT)
            self.assert_private(payload, tokens=(pending["verification_token"],))
            portable_settings = json.loads(payload["tables"]["app_model_settings"][0]["settings_json"])
            self.assertNotIn("_pending_connection_probes", portable_settings)
            self.assertNotIn(token_hash, json.dumps(portable_settings))
            for entry in portable_settings["connections"]:
                self.assertIsNone(entry.get("_credential_ref"))
                self.assertIsNone(entry.get("last_probe"))
            conn.commit()
            artifact = backups.create(conn, self.database, self.root / "backups", PROJECT, BRANCH, STAMP)
        package = (self.root / "backups" / (artifact["id"] + ".zip")).read_bytes()
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertEqual(set(archive.namelist()), {"project.json", "manifest.json"})
            for name in archive.namelist():
                self.assert_private(archive.read(name).decode("utf-8"),
                                    tokens=(pending["verification_token"],))
        self.assert_private(self.database.read_bytes().decode("utf-8", errors="ignore"),
                            tokens=(pending["verification_token"],))
        calls_before = sum(len(provider.calls) for provider, _ in self.providers)
        self.save_probe(self.probe(1, id=second["id"], model="updated-second"))
        with self.connect() as conn, self.assertRaises(ValueError):
            server.retry_provider_config(conn, frozen)
        self.assertEqual(sum(len(provider.calls) for provider, _ in self.providers), calls_before + 1)

    def test_backup_restore_requires_retest_before_use_then_can_be_reenabled(self) -> None:
        first, second = self.saved(), self.saved(1)
        self.assign(first["id"], {"polish": second["id"]})
        pending = self.probe()
        restored_project = "model-library-restored"
        with self.connect() as conn:
            payload = backups.capture(conn, PROJECT)
            backups.validate_payload(conn, payload)
            backups.transplant(conn, payload, restored_project)
        with self.connect() as conn:
            public = settings.public_settings(conn, restored_project)
            self.assertEqual(len(public["connections"]), 2)
            self.assertFalse(public["configured"])
            self.assertEqual(public["default_connection_id"], "")
            self.assertEqual(public["task_connections"], {})
            self.assertTrue(all(not entry["verified"] for entry in public["connections"]))
            self.assertTrue(all(not entry["api_key_configured"] for entry in public["connections"]))
            with self.assertRaises(ValueError):
                settings.save_settings(conn, restored_project, {
                    "default_connection_id": public["connections"][0]["id"],
                })
        with self.assertRaises(ValueError):
            self.save_probe(pending, restored_project)
        identifier = public["connections"][0]["id"]
        refreshed = self.save_probe(
            self.probe(project=restored_project, id=identifier), restored_project)["connection"]
        self.assertTrue(refreshed["verified"])
        self.assertEqual(refreshed["id"], identifier)
        with self.connect() as conn:
            result = settings.save_settings(conn, restored_project, {
                "default_connection_id": identifier,
            })
            resolved = settings.task_provider_settings(
                settings.provider_settings(conn, restored_project), "continue")
        self.assertTrue(result["configured"])
        self.assertTrue(resolved["api_key"] == FIRST_KEY)

    def test_http_flow_requires_receipt_and_keeps_public_responses_private(self) -> None:
        status, result = self.request("POST", "/api/ai/connections", {"name": "untested"})
        self.assertEqual(status, 400)
        self.assert_private(result)
        status, probe = self.request("POST", "/api/ai/connections/probe", {
            "name": "HTTP connection", "base_url": self.bases[1] + "/normal",
            "model": "second-model", "api_key": SECOND_KEY,
        })
        self.assertEqual(status, 200)
        self.assertTrue(probe["probe"]["success"])
        self.assert_private(probe)
        status, result = self.request("POST", "/api/ai/connections", {
            "verification_token": probe["verification_token"], "name": "saved HTTP connection",
        })
        self.assertEqual(status, 200)
        connection = result["connection"]
        self.assert_private(result, tokens=(probe["verification_token"],))
        self.assign(connection["id"])
        for path in ("/api/ai/connections", "/api/ai/settings", "/api/state"):
            status, result = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assert_private(result, tokens=(probe["verification_token"],))
        status, result = self.request("POST", "/api/ai/connections", {
            "verification_token": probe["verification_token"],
        })
        self.assertEqual(status, 400)
        self.assert_private(result, tokens=(probe["verification_token"],))
        status, result = self.request("DELETE", "/api/ai/connections/" + connection["id"])
        self.assertEqual(status, 400)
        self.assert_private(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
