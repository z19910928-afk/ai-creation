"""Probe verification against an isolated database and free local provider."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MOYU_AI_BASE_URL"] = ""
os.environ["MOYU_AI_MODEL"] = ""
os.environ["MOYU_AI_API_KEY"] = ""

import moyu_settings
import server


class Provider(BaseHTTPRequestHandler):
    calls: list[dict] = []
    started = threading.Event()
    release = threading.Event()

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        case = self.path.split("/")[1]
        self.calls.append({"case": case, "payload": payload})
        if case == "held":
            self.started.set()
            self.release.wait(timeout=3)
        self.send_response(401 if case == "unauthorized" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if case == "unauthorized":
            body = {"error": "fixture unauthorized"}
        else:
            message = {"content": "铁" if case == "one-character" else
                       "连接正常。" if case == "extra-punctuation" else "连接正常"}
            if case in {"reasoning", "reasoning-allowed"}:
                message["reasoning_content"] = "fixture private reasoning"
            elif case == "reasoning-alias":
                message["reasoning"] = "fixture private reasoning"
            elif case == "reasoning-object":
                message["reasoning_content"] = {"text": "fixture private reasoning"}
            elif case == "reasoning-array":
                message["reasoning"] = []
            elif case == "reasoning-null":
                message["reasoning"] = None
            budget_limited = (case == "reasoning-budget"
                              and payload.get("max_completion_tokens", payload.get("max_tokens", 0)) < 128)
            body = {
                "model": "different-fixture" if case == "model-mismatch" else payload["model"],
                "choices": [{"message": message, "finish_reason": "length"
                             if case == "truncated" or budget_limited else "stop"}],
                "usage": {"completion_tokens": 7} if case == "partial-usage" else
                         {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20},
            }
            if case == "model-unreported":
                body.pop("model")
            if self.path.endswith("/responses"):
                body = {
                    **({"model": body["model"]} if "model" in body else {}),
                    "id": "resp-settings-fixture", "object": "response",
                    "status": "completed",
                    "output": [{"id": "msg-settings-fixture", "type": "message", "role": "assistant",
                                "status": "completed", "content": [
                                    {"type": "output_text", "text": message["content"], "annotations": []}]}],
                    "usage": {"input_tokens": 13, "output_tokens": 7, "total_tokens": 20},
                }
        try:
            self.wfile.write(json.dumps(body, ensure_ascii=False).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args: object) -> None:
        return


class ModelSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        cls.provider.daemon_threads = True
        cls.thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.provider.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.thread.join(timeout=3)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="moyu-settings-fixture-")
        self.root = Path(self.temporary.name)
        server.DB_PATH = self.root / "book.sqlite3"
        self.previous_credentials = os.environ.get("MOYU_CREDENTIALS_DIR")
        os.environ["MOYU_CREDENTIALS_DIR"] = str(self.root / "credentials")
        server.init_db()
        with server.connect() as conn:
            server.ensure_seed(conn)
        self.project = server.PROJECT_ID
        self.probe_threads: list[threading.Thread] = []

    def tearDown(self) -> None:
        Provider.release.set()
        for thread in self.probe_threads:
            thread.join(timeout=3)
        self.temporary.cleanup()
        if self.previous_credentials is None:
            os.environ.pop("MOYU_CREDENTIALS_DIR", None)
        else:
            os.environ["MOYU_CREDENTIALS_DIR"] = self.previous_credentials

    def settings(self, case: str = "normal") -> None:
        with server.connect() as conn:
            moyu_settings.save_settings(conn, self.project, {
                "base_url": self.base + "/" + case, "model": "requested-fixture",
                "parameters": {"temperature": 0, "max_tokens": 3000,
                               "thinking": {"type": "enabled" if case in {
                                   "reasoning-allowed", "reasoning-budget"} else "disabled"}},
                "daily_limit": 100,
            })

    def probe(self, case: str = "normal") -> dict:
        self.settings(case)
        with server.connect() as conn:
            response = moyu_settings.probe_settings(conn, self.project)
            row = conn.execute("SELECT status FROM ai_run WHERE id=?", (response["ai_run_id"],)).fetchone()
            response["run_status"] = row["status"]
        return response

    def test_valid_reply_matches_requested_model_and_current_rules(self) -> None:
        result = self.probe()
        probe = result["probe"]
        self.assertTrue(probe["success"])
        self.assertTrue(probe["protocol_complete"])
        self.assertTrue(probe["response_valid"])
        self.assertTrue(probe["reasoning_consistent"])
        self.assertTrue(probe["model_identity_match"])
        self.assertEqual(probe["identity_status"], "exact")
        self.assertEqual(result["run_status"], "success")
        self.assertEqual(result["settings"]["last_probe"], probe)
        self.assertEqual(Provider.calls[-1]["payload"]["max_tokens"], moyu_settings.PROBE_MAX_TOKENS)
        self.assertEqual(Provider.calls[-1]["payload"]["temperature"], 0)

    def test_one_character_stop_records_completion_but_fails_response(self) -> None:
        result = self.probe("one-character")
        probe = result["probe"]
        self.assertTrue(probe["protocol_complete"])
        self.assertEqual(probe["completion_state"], "complete")
        self.assertEqual(probe["finish_reason"], "stop")
        self.assertFalse(probe["response_valid"])
        self.assertFalse(probe["success"])
        self.assertEqual(probe["response_characters"], 1)
        self.assertEqual(probe["error_code"], "probe_unexpected_response")
        self.assertEqual(result["run_status"], "failed")
        self.assertEqual(probe["usage"]["total_tokens"], 20)
        self.assertEqual(result["settings"]["last_probe"], probe)
        with server.connect() as conn:
            reservation = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                       (result["ai_run_id"],)).fetchone()
        self.assertEqual(reservation["status"], "consumed")

    def test_extra_punctuation_is_not_the_exact_requested_reply(self) -> None:
        probe = self.probe("extra-punctuation")["probe"]
        self.assertTrue(probe["protocol_complete"])
        self.assertFalse(probe["response_valid"])
        self.assertFalse(probe["success"])

    def test_model_mismatch_is_preserved_and_not_reported_healthy(self) -> None:
        probe = self.probe("model-mismatch")["probe"]
        self.assertTrue(probe["protocol_complete"])
        self.assertTrue(probe["response_valid"])
        self.assertEqual(probe["requested_model"], "requested-fixture")
        self.assertEqual(probe["returned_model"], "different-fixture")
        self.assertFalse(probe["model_identity_match"])
        self.assertEqual(probe["identity_status"], "mismatch")
        self.assertFalse(probe["success"])
        self.assertEqual(probe["error_code"], "probe_model_mismatch")

    def test_missing_model_name_keeps_connection_usable_without_claiming_identity(self) -> None:
        result = self.probe("model-unreported")
        probe = result["probe"]
        self.assertTrue(probe["response_valid"])
        self.assertIsNone(probe["model_identity_match"])
        self.assertEqual(probe["identity_status"], "unreported")
        self.assertTrue(probe["success"])
        self.assertNotIn("error_code", probe)
        self.assertEqual(result["run_status"], "success")
        self.assertEqual(result["settings"]["last_probe"], probe)

    def test_reasoning_probe_has_sufficient_budget_without_retrying(self) -> None:
        before = len(Provider.calls)
        result = self.probe("reasoning-budget")
        self.assertTrue(result["probe"]["success"])
        self.assertEqual(len(Provider.calls), before + 1)
        self.assertGreaterEqual(Provider.calls[-1]["payload"]["max_tokens"], 128)

    def test_connection_options_validate_and_flow_into_probe_and_frozen_config(self) -> None:
        self.settings()
        with server.connect() as conn:
            public = moyu_settings.save_settings(conn, self.project, {
                "output_token_parameter": "max_completion_tokens", "include_usage": True,
            })
            config = moyu_settings.provider_settings(conn, self.project)
            frozen = moyu_settings.frozen_provider_settings(
                conn, self.project, {name: value for name, value in config.items() if name != "api_key"})
            result = moyu_settings.probe_settings(conn, self.project)
        self.assertEqual(public["output_token_parameter"], "max_completion_tokens")
        self.assertTrue(public["include_usage"])
        self.assertEqual(frozen["output_token_parameter"], "max_completion_tokens")
        self.assertTrue(frozen["include_usage"])
        self.assertTrue(result["probe"]["success"])
        payload = Provider.calls[-1]["payload"]
        self.assertEqual(payload["max_completion_tokens"], moyu_settings.PROBE_MAX_TOKENS)
        self.assertNotIn("max_tokens", payload)
        with server.connect() as conn:
            changed = moyu_settings.save_settings(conn, self.project, {"include_usage": False})
            self.assertIsNone(changed["last_probe"])
            for invalid in ({"output_token_parameter": "other"}, {"output_token_parameter": []},
                            {"include_usage": 1}, {"include_usage": None},
                            {"api_protocol": "other"}, {"api_protocol": []}, {"api_protocol": None}):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    moyu_settings.save_settings(conn, self.project, invalid)

    def test_protocol_is_explicit_in_probe_public_settings_and_old_frozen_snapshots(self) -> None:
        self.settings()
        with server.connect() as conn:
            previous = moyu_settings.provider_settings(conn, self.project)
            snapshot = {name: value for name, value in previous.items()
                        if name not in {"api_key", "api_protocol"}}
            self.assertEqual(moyu_settings.frozen_provider_settings(
                conn, self.project, snapshot)["api_protocol"], "chat_completions")
            public = moyu_settings.save_settings(conn, self.project, {
                "api_protocol": "responses", "parameters": {"temperature": 0, "max_tokens": 3000},
            })
            result = moyu_settings.probe_settings(conn, self.project)
            self.assertTrue(result["probe"]["success"], result)
            config = moyu_settings.provider_settings(conn, self.project)
            self.assertEqual(config["api_protocol"], "responses")
            self.assertEqual(public["api_protocol"], "responses")
            self.assertEqual(result["settings"]["api_protocol"], "responses")
            payload = Provider.calls[-1]["payload"]
            self.assertIn("input", payload)
            self.assertNotIn("messages", payload)
            self.assertEqual(payload["max_output_tokens"], moyu_settings.PROBE_MAX_TOKENS)
            changed = moyu_settings.save_settings(conn, self.project, {"api_protocol": "chat_completions"})
            self.assertIsNone(changed["last_probe"])

    def test_disabled_thinking_with_reasoning_fails_without_exposing_reasoning(self) -> None:
        probe = self.probe("reasoning")["probe"]
        self.assertTrue(probe["protocol_complete"])
        self.assertTrue(probe["response_valid"])
        self.assertFalse(probe["reasoning_consistent"])
        self.assertFalse(probe["success"])
        self.assertEqual(probe["error_code"], "probe_unexpected_reasoning")
        self.assertNotIn("fixture private reasoning", json.dumps(probe))

    def test_requested_reasoning_does_not_fail_a_valid_probe(self) -> None:
        probe = self.probe("reasoning-allowed")["probe"]
        self.assertTrue(probe["reasoning_consistent"])
        self.assertTrue(probe["success"])

    def test_reasoning_alias_and_unknown_shapes_fail_probe_but_null_is_allowed(self) -> None:
        for case in ("reasoning-alias", "reasoning-object", "reasoning-array"):
            with self.subTest(case=case):
                result = self.probe(case)
                probe = result["probe"]
                self.assertTrue(probe["protocol_complete"])
                self.assertTrue(probe["response_valid"])
                self.assertFalse(probe["reasoning_consistent"])
                self.assertFalse(probe["success"])
                self.assertEqual(probe["error_code"], "probe_unexpected_reasoning")
                self.assertEqual(probe["usage"]["total_tokens"], 20)
                self.assertEqual(result["run_status"], "failed")
                self.assertNotIn("fixture private reasoning", json.dumps(result))
                if case != "reasoning-alias":
                    self.assertIn("思考字段类型无法校验", probe["error"])
                    self.assertNotIn("非空", probe["error"])
        self.assertTrue(self.probe("reasoning-null")["probe"]["success"])

    def test_length_finish_is_not_protocol_complete(self) -> None:
        result = self.probe("truncated")
        self.assertFalse(result["probe"]["protocol_complete"])
        self.assertFalse(result["probe"]["success"])
        self.assertEqual(result["probe"]["error_code"], "probe_incomplete_response")
        self.assertEqual(result["run_status"], "unknown")

    def test_authorization_failure_is_saved_with_unknown_charge(self) -> None:
        result = self.probe("unauthorized")
        self.assertFalse(result["probe"]["success"])
        self.assertEqual(result["probe"]["error_code"], "provider_http_error")
        self.assertEqual(result["probe"]["billing_state"], "unknown")
        self.assertEqual(result["settings"]["last_probe"], result["probe"])

    def test_partial_usage_does_not_turn_known_connection_into_failure(self) -> None:
        result = self.probe("partial-usage")
        self.assertTrue(result["probe"]["success"])
        self.assertEqual(result["probe"]["billing_state"], "unknown")
        with server.connect() as conn:
            reservation = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                       (result["ai_run_id"],)).fetchone()
        self.assertEqual(reservation["status"], "unknown")

    def test_same_settings_preserve_valid_probe_but_model_change_clears_it(self) -> None:
        result = self.probe()
        with server.connect() as conn:
            same = moyu_settings.save_settings(conn, self.project, {"model": "requested-fixture"})
            changed = moyu_settings.save_settings(conn, self.project, {"model": "replacement-fixture"})
        self.assertEqual(same["last_probe"], result["probe"])
        self.assertIsNone(changed["last_probe"])

    def test_configuration_change_during_probe_invalidates_success(self) -> None:
        Provider.started.clear()
        Provider.release.clear()
        self.settings("held")
        results = []
        def work() -> None:
            with server.connect() as conn:
                results.append(moyu_settings.probe_settings(conn, self.project))
        thread = threading.Thread(target=work)
        self.probe_threads.append(thread)
        thread.start()
        self.assertTrue(Provider.started.wait(timeout=2))
        with server.connect() as conn:
            moyu_settings.save_settings(conn, self.project, {"model": "replacement-fixture"})
        Provider.release.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        probe = results[0]["probe"]
        self.assertTrue(probe["protocol_complete"])
        self.assertTrue(probe["response_valid"])
        self.assertTrue(probe["configuration_stale"])
        self.assertFalse(probe["success"])
        self.assertEqual(probe["error_code"], "probe_configuration_stale")
        self.assertEqual(results[0]["settings"]["model"], "replacement-fixture")
        self.assertIsNone(results[0]["settings"]["last_probe"])

    def test_legacy_stop_only_success_is_read_as_unverified_without_rewriting_audit(self) -> None:
        self.settings()
        with server.connect() as conn:
            saved = json.loads(conn.execute("SELECT settings_json FROM app_model_settings WHERE project_id=?",
                                           (self.project,)).fetchone()[0])
            saved["last_probe"] = {"success": True, "model": "requested-fixture",
                                   "finish_reason": "stop", "checked_at": "2026-10-13T00:00:00+00:00"}
            conn.execute("UPDATE app_model_settings SET settings_json=? WHERE project_id=?",
                         (json.dumps(saved, ensure_ascii=False), self.project))
            public = moyu_settings.public_settings(conn, self.project)
            stored = json.loads(conn.execute("SELECT settings_json FROM app_model_settings WHERE project_id=?",
                                            (self.project,)).fetchone()[0])
        self.assertFalse(public["last_probe"]["success"])
        self.assertEqual(public["last_probe"]["error_code"], "probe_legacy_unverified")
        self.assertEqual(stored["last_probe"], saved["last_probe"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
