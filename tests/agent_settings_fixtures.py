"""Agent settings against isolated SQLite and a free loopback provider."""
from __future__ import annotations

import json
import hashlib
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
import moyu_settings
import moyu_workspace
from moyu_provider import call_chat_completion

MAIN_KEY = "synthetic-main-connection-private-key"
AGENT_KEY = "synthetic-agent-connection-private-key"


class Provider(BaseHTTPRequestHandler):
    calls: list[dict] = []
    database: Path | None = None
    started = threading.Event()
    release = threading.Event()

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        case = self.path.split("/")[1]
        request_id = self.headers.get("X-Client-Request-Id")
        with sqlite3.connect(self.database) as conn:
            ledger = conn.execute(
                "SELECT q.status,a.consent_json FROM ai_run a "
                "JOIN app_quota_reservation q ON q.ai_run_id=a.id WHERE a.id=?", (request_id,),
            ).fetchone()
        self.calls.append({"case": case, "payload": payload,
                           "authorization": self.headers.get("Authorization"),
                           "ledger": (ledger[0], json.loads(ledger[1])) if ledger else None})
        if case == "held":
            self.started.set()
            self.release.wait(timeout=5)
        self.send_response(401 if case == "unauthorized" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        message = {"content": "连接正常。" if case == "extra" else "连接正常"}
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
        try:
            self.wfile.write(json.dumps(body, ensure_ascii=False).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args: object) -> None:
        return


class AgentSettingsTests(unittest.TestCase):
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
        Provider.started.clear()
        Provider.release.clear()
        self.probe_threads: list[threading.Thread] = []
        with self.connect() as conn:
            conn.executescript((ROOT / "docs/schema-v1.sql").read_text(encoding="utf-8"))
            for column in ("context_item_ids_json TEXT", "token_json TEXT", "cost_json TEXT",
                           "author_decision TEXT", "consent_json TEXT", "latency_ms INTEGER"):
                conn.execute("ALTER TABLE ai_run ADD COLUMN " + column)
            conn.execute("CREATE TABLE app_state_version(project_id TEXT PRIMARY KEY,version INTEGER NOT NULL DEFAULT 0)")
            moyu_workspace.install_schema(conn)
            agent.install_schema(conn)
            agent.install_schema(conn)
            conn.execute("INSERT INTO project VALUES(?,1,'fixture','2026-10-16')", (self.project,))
            conn.execute("INSERT INTO branch(id,project_id,status) VALUES('agent-main',?,'active')", (self.project,))
            conn.execute("INSERT INTO app_state_version(project_id) VALUES(?)", (self.project,))

    def tearDown(self) -> None:
        Provider.release.set()
        for thread in self.probe_threads:
            thread.join(timeout=5)
        self.environment.stop()
        self.temporary.cleanup()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def save_main(self, **overrides) -> dict:
        with self.connect() as conn:
            return moyu_settings.save_settings(conn, self.project, {
                "base_url": self.base + "/main", "model": "main-fixture", "api_key": MAIN_KEY,
                "daily_limit": 100, **overrides,
            })

    def save_independent(self, case="normal", **overrides) -> dict:
        with self.connect() as conn:
            return agent.save_settings(conn, self.project, {
                "mode": "independent", "base_url": self.base + "/" + case,
                "model": "agent-fixture", "api_key": AGENT_KEY,
                "parameters": {"temperature": 0, "max_tokens": 3000, "thinking": {"type": "disabled"}},
                **overrides,
            })

    def probe(self) -> dict:
        with self.connect() as conn:
            return agent.probe(conn, self.project)

    def stored_main(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT settings_json FROM app_model_settings WHERE project_id=?",
                               (self.project,)).fetchone()
            return row[0] if row else None

    def test_default_inherits_main_and_never_exposes_plaintext_key(self) -> None:
        self.save_main(parameters={"temperature": 0, "max_tokens": 1200})
        with self.connect() as conn:
            public = agent.public_settings(conn, self.project)
            config = agent.provider_settings(conn, self.project)
        self.assertEqual(public["mode"], "inherit")
        self.assertTrue(public["configured"])
        self.assertTrue(public["is_configured"])
        self.assertEqual(public["model"], "main-fixture")
        self.assertEqual(public["parameters"], {"temperature": 0, "max_tokens": 1200})
        self.assertEqual(config["api_key"], MAIN_KEY)
        self.assertNotIn(MAIN_KEY, json.dumps(public))
        self.assertNotIn("_credential_ref", json.dumps(public))

    def test_independent_connection_preserves_main_and_frozen_credentials(self) -> None:
        self.save_main(daily_limit=9)
        before = self.stored_main()
        public = self.save_independent()
        with self.connect() as conn:
            config = agent.provider_settings(conn, self.project)
            frozen = moyu_settings.frozen_provider_settings(
                conn, self.project, {key: value for key, value in config.items() if key != "api_key"})
            main = moyu_settings.provider_settings(conn, self.project)
            raw = conn.execute("SELECT settings_json FROM app_agent_connection").fetchone()[0]
        self.assertEqual(before, self.stored_main())
        self.assertEqual(config["api_key"], AGENT_KEY)
        self.assertEqual(frozen["api_key"], AGENT_KEY)
        self.assertEqual(main["api_key"], MAIN_KEY)
        self.assertEqual(public["daily_limit"], 9)
        self.assertNotIn(AGENT_KEY, raw)
        self.assertNotIn(AGENT_KEY, json.dumps(public))
        self.assertEqual(Provider.calls, [])

    def test_switching_modes_keeps_independent_connection_and_key(self) -> None:
        self.save_main()
        self.save_independent()
        with self.connect() as conn:
            inherited = agent.save_settings(conn, self.project, {"mode": "inherit"})
            self.assertEqual(agent.provider_settings(conn, self.project)["api_key"], MAIN_KEY)
            restored = agent.save_settings(conn, self.project, {"mode": "independent"})
            self.assertEqual(agent.provider_settings(conn, self.project)["api_key"], AGENT_KEY)
        self.assertEqual(inherited["model"], "main-fixture")
        self.assertEqual(inherited["independent_settings"]["model"], "agent-fixture")
        self.assertEqual(restored["model"], "agent-fixture")

    def test_validation_accepts_zero_and_rejects_unsafe_or_unknown_values(self) -> None:
        self.save_independent()
        with self.connect() as conn:
            for payload in (
                {"parameters": {"temperature": -0.1}},
                {"parameters": {"max_tokens": True}},
                {"parameters": {"top_p": 2}},
                {"parameters": {"thinking": {"type": "unknown"}}},
                {"parameters": {"unknown": "value"}},
                {"mode": "other"},
                {"daily_limit": 999},
                {"base_url": "http://example.com/v1"},
                {"api_key": "invalid\nkey"},
                {"clear_api_key": "yes"},
                {"clear_api_key": True, "api_key": AGENT_KEY},
                {"output_token_parameter": "unknown"},
                {"output_token_parameter": []},
                {"include_usage": 1},
            ):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    agent.save_settings(conn, self.project, payload)
            self.assertEqual(agent.provider_settings(conn, self.project)["parameters"]["temperature"], 0)

    def test_probe_records_dispatch_before_send_and_only_updates_agent_settings(self) -> None:
        self.save_main()
        before = self.stored_main()
        self.save_independent()
        result = self.probe()
        self.assertTrue(result["probe"]["success"], result)
        self.assertEqual(result["settings"]["last_probe"], result["probe"])
        self.assertEqual(before, self.stored_main())
        call = Provider.calls[-1]
        self.assertEqual(call["authorization"], "Bearer " + AGENT_KEY)
        self.assertEqual(call["payload"]["max_tokens"], moyu_settings.PROBE_MAX_TOKENS)
        self.assertEqual(call["payload"]["temperature"], 0)
        self.assertEqual(call["ledger"][0], "dispatched")
        self.assertEqual(call["ledger"][1]["connection_scope"], "agent_chat")
        self.assertTrue(call["ledger"][1]["request_audit"]["dispatched"])
        with self.connect() as conn:
            row = conn.execute(
                "SELECT a.status,q.status quota,a.consent_json FROM ai_run a "
                "JOIN app_quota_reservation q ON q.ai_run_id=a.id WHERE a.id=?", (result["ai_run_id"],),
            ).fetchone()
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["quota"], "consumed")
        self.assertNotIn(AGENT_KEY, row["consent_json"])

    def test_same_settings_and_key_preserve_probe_but_connection_changes_clear_it(self) -> None:
        self.save_independent()
        result = self.probe()
        with self.connect() as conn:
            same = agent.save_settings(conn, self.project, {"model": "agent-fixture", "api_key": AGENT_KEY})
            changed = agent.save_settings(conn, self.project, {"model": "replacement-fixture"})
        self.assertEqual(same["last_probe"], result["probe"])
        self.assertIsNone(changed["last_probe"])

    def test_inherited_probe_expires_when_main_connection_changes(self) -> None:
        self.save_main()
        result = self.probe()
        self.assertTrue(result["probe"]["success"])
        with self.connect() as conn:
            moyu_settings.save_settings(conn, self.project, {"model": "replacement-fixture"})
            public = agent.public_settings(conn, self.project)
            stored = json.loads(conn.execute("SELECT settings_json FROM app_agent_connection").fetchone()[0])
        self.assertIsNone(public["last_probe"])
        self.assertTrue(public["probe_stale"])
        self.assertEqual(stored["last_probe"], result["probe"])

    def test_failed_protocol_and_identity_checks_never_report_healthy(self) -> None:
        for case, code in (
            ("mismatch", "probe_model_mismatch"),
            ("extra", "probe_unexpected_response"), ("truncated", "probe_incomplete_response"),
            ("reasoning", "probe_unexpected_reasoning"),
            ("invalid-reasoning", "probe_unexpected_reasoning"), ("unauthorized", "provider_http_error"),
        ):
            with self.subTest(case=case):
                self.save_independent(case)
                result = self.probe()
                self.assertFalse(result["probe"]["success"])
                self.assertEqual(result["probe"]["error_code"], code)
                self.assertEqual(result["settings"]["last_probe"], result["probe"])
                self.assertNotIn("fixture-private-reasoning", json.dumps(result))
                self.assertNotIn(AGENT_KEY, json.dumps(result))

    def test_unreported_model_is_usable_without_claiming_identity_verification(self) -> None:
        self.save_independent("unreported")
        result = self.probe()
        self.assertTrue(result["probe"]["success"])
        self.assertEqual(result["probe"]["identity_status"], "unreported")
        self.assertIsNone(result["probe"]["model_identity_match"])
        self.assertNotIn("error_code", result["probe"])
        self.assertEqual(result["settings"]["last_probe"], result["probe"])

    def test_independent_connection_options_reach_actual_calls_and_frozen_settings(self) -> None:
        self.save_main(output_token_parameter="max_tokens", include_usage=False)
        public = self.save_independent(output_token_parameter="max_completion_tokens", include_usage=True)
        self.assertEqual(public["output_token_parameter"], "max_completion_tokens")
        self.assertTrue(public["independent_settings"]["include_usage"])
        self.assertTrue(self.probe()["probe"]["success"])
        self.assertEqual(Provider.calls[-1]["payload"]["max_completion_tokens"], moyu_settings.PROBE_MAX_TOKENS)
        self.assertNotIn("max_tokens", Provider.calls[-1]["payload"])
        with self.connect() as conn:
            config = agent.provider_settings(conn, self.project)
            frozen = moyu_settings.frozen_provider_settings(
                conn, self.project, {name: value for name, value in config.items() if name != "api_key"})
        self.assertEqual(frozen["output_token_parameter"], "max_completion_tokens")
        self.assertTrue(frozen["include_usage"])
        call_chat_completion(
            frozen, [{"role": "user", "content": "synthetic chat"}],
            task="agent_chat", max_tokens=123, stream=True)
        payload = Provider.calls[-1]["payload"]
        self.assertEqual(payload["max_completion_tokens"], 123)
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        with self.connect() as conn:
            inherited = agent.save_settings(conn, self.project, {"mode": "inherit"})
            changed = agent.save_settings(conn, self.project, {
                "mode": "independent", "include_usage": False})
        self.assertEqual(inherited["output_token_parameter"], "max_tokens")
        self.assertFalse(inherited["include_usage"])
        self.assertIsNone(changed["last_probe"])

    def test_legacy_default_probe_signature_remains_valid_until_options_change(self) -> None:
        self.save_independent()
        self.probe()
        with self.connect() as conn:
            config = agent.provider_settings(conn, self.project)
            fields = ("mode", "base_url", "model", "parameters", "max_tokens", "credential_fingerprint")
            original = {name: config.get(name) for name in fields}
            original["output_token_parameter"] = None
            signature = hashlib.sha256(json.dumps(
                original, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":")).encode()).hexdigest()
            saved = json.loads(conn.execute("SELECT settings_json FROM app_agent_connection").fetchone()[0])
            saved.pop("output_token_parameter")
            saved.pop("include_usage")
            saved["_probe_signature"] = signature
            conn.execute("UPDATE app_agent_connection SET settings_json=?", (json.dumps(saved),))
            before = conn.total_changes
            public = agent.public_settings(conn, self.project)
            self.assertEqual(before, conn.total_changes)
            self.assertTrue(public["last_probe"]["success"])
            self.assertFalse(public["probe_stale"])
            saved["include_usage"] = True
            conn.execute("UPDATE app_agent_connection SET settings_json=?", (json.dumps(saved),))
            changed = agent.public_settings(conn, self.project)
        self.assertTrue(changed["probe_stale"])
        self.assertIsNone(changed["last_probe"])

    def test_partial_usage_keeps_connection_verified_and_charge_unknown(self) -> None:
        self.save_independent("partial-usage")
        result = self.probe()
        self.assertTrue(result["probe"]["success"])
        self.assertEqual(result["probe"]["billing_state"], "unknown")
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                          (result["ai_run_id"],)).fetchone()[0], "unknown")

    def test_agent_and_main_probe_share_daily_quota(self) -> None:
        self.save_main(daily_limit=1)
        with self.connect() as conn:
            self.assertTrue(moyu_settings.probe_settings(conn, self.project)["probe"]["success"])
        self.save_independent()
        before = len(Provider.calls)
        with self.assertRaisesRegex(ValueError, "共享生成上限"):
            self.probe()
        self.assertEqual(len(Provider.calls), before)

    def test_canceled_before_send_releases_shared_slot(self) -> None:
        self.save_independent()
        cancel = threading.Event()
        cancel.set()
        with self.connect() as conn:
            result = agent.probe(conn, self.project, cancel_event=cancel)
            reservation = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                       (result["ai_run_id"],)).fetchone()[0]
        self.assertFalse(result["probe"]["success"])
        self.assertEqual(result["probe"]["billing_state"], "not_dispatched")
        self.assertEqual(reservation, "released")
        self.assertEqual(Provider.calls, [])

    def test_configuration_change_during_probe_does_not_overwrite_latest_settings(self) -> None:
        self.save_independent("held")
        results, errors = [], []

        def work() -> None:
            try:
                with self.connect() as conn:
                    results.append(agent.probe(conn, self.project))
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=work)
        self.probe_threads.append(thread)
        thread.start()
        self.assertTrue(Provider.started.wait(timeout=3))
        with self.connect() as conn:
            agent.save_settings(conn, self.project, {"model": "replacement-fixture"})
        Provider.release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        check = results[0]["probe"]
        self.assertTrue(check["configuration_stale"])
        self.assertFalse(check["success"])
        self.assertEqual(check["error_code"], "probe_configuration_stale")
        self.assertEqual(results[0]["settings"]["model"], "replacement-fixture")
        self.assertIsNone(results[0]["settings"]["last_probe"])

    def test_key_files_are_private_and_references_cannot_cross_projects(self) -> None:
        self.save_independent()
        directory = self.root / "credentials"
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        files = list(directory.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        with self.connect() as conn:
            conn.execute("INSERT INTO project VALUES('another-project',1,'fixture','2026-10-16')")
            raw = conn.execute("SELECT settings_json FROM app_agent_connection").fetchone()[0]
            conn.execute("INSERT INTO app_agent_connection VALUES('another-project',?,'2026-10-16')", (raw,))
            with self.assertRaisesRegex(ValueError, "其他作品"):
                agent.public_settings(conn, "another-project")
        files[0].chmod(0o644)
        with self.connect() as conn, self.assertRaisesRegex(ValueError, "权限"):
            agent.public_settings(conn, self.project)
        files[0].chmod(0o600)
        with self.connect() as conn:
            cleared = agent.save_settings(conn, self.project, {"clear_api_key": True})
            self.assertFalse(cleared["api_key_configured"])
            self.assertEqual(agent.provider_settings(conn, self.project)["api_key"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
