"""Exercise the source or frozen desktop service using synthetic local data."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import queue
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_COMMAND = [sys.executable, "-B", str(ROOT / "desktop" / "backend.py")]
STATIC_FILES = (
    "index.html", "app.js", "editor-sync.js", "author-workspace.js", "styles.css",
    "knowledge-workspace.js", "writing-inbox.js", "knowledge-workspace.css",
    "editor-preferences.css", "agent-chat.js", "agent-chat.css", "agent-settings.js",
    "agent-settings.css",
)
BODY = "Desktop fixture manuscript. A door opens in the rain."


class UnexpectedProvider(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        type(self).calls += 1
        self.send_response(500)
        self.end_headers()


class GoodProvider(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.server.calls.append({
            "path": self.path, "payload": payload,
            "authorization": self.headers.get("Authorization"),
        })
        raw = json.dumps({
            "model": payload["model"],
            "choices": [{"message": {"content": "\u8fde\u63a5\u6b63\u5e38"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Backend:
    def __init__(self, root, provider_port):
        self.root = root
        self.data = root / "private-data"
        self.token = secrets.token_urlsafe(40)
        self.stdout = []
        self.stderr = []
        self.lines = queue.Queue()
        self.process = None
        self.port = None
        keep = {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "TMPDIR", "LANG", "LC_ALL", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH",
        }
        self.environment = {
            key: value for key, value in os.environ.items() if key.upper() in keep
        }
        self.environment.update({
            "HOME": str(root / "home"),
            "USERPROFILE": str(root / "home"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "MOYU_DESKTOP_DATA_DIR": str(self.data),
            "MOYU_DESKTOP_TOKEN": self.token,
            "MOYU_DESKTOP_PORT": "0",
            "MOYU_DESKTOP_PARENT_PIPE": "1",
            "MOYU_DB": str(root / "inherited.sqlite3"),
            "MOYU_CREDENTIALS_DIR": str(root / "inherited-credentials"),
            "MOYU_HOST": "0.0.0.0",
            "MOYU_PORT": "1",
            "MOYU_AI_BASE_URL": f"http://127.0.0.1:{provider_port}/v1",
            "MOYU_AI_API_KEY": "fixture-inherited-key",
            "MOYU_AI_MODEL": "fixture-inherited-model",
            "MOYU_AI_TEMPERATURE": "invalid-shell-default",
            "MOYU_AI_DAILY_QUOTA": "invalid-shell-default",
        })

    def start(self):
        self.stdout = []
        self.stderr = []
        self.lines = queue.Queue()
        self.process = subprocess.Popen(
            BACKEND_COMMAND, cwd=self.root, env=self.environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )

        def collect(stream, target, ready=False):
            for line in stream:
                target.append(line)
                if ready:
                    self.lines.put(line)

        self.readers = [
            threading.Thread(target=collect, args=(self.process.stdout, self.stdout, True),
                             daemon=True),
            threading.Thread(target=collect, args=(self.process.stderr, self.stderr),
                             daemon=True),
        ]
        for reader in self.readers:
            reader.start()
        try:
            value = json.loads(self.lines.get(timeout=30))
        except (queue.Empty, json.JSONDecodeError):
            self.stop()
            raise AssertionError("Desktop backend did not emit a valid ready receipt")
        if (set(value) != {"type", "port", "pid"} or value["type"] != "moyu-ready"
                or value["pid"] != self.process.pid
                or not isinstance(value["port"], int) or not 0 < value["port"] < 65536):
            self.stop()
            raise AssertionError("Desktop backend emitted an invalid ready receipt")
        self.port = value["port"]
        return self

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            if self.process.stdin is not None:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        for reader in getattr(self, "readers", []):
            reader.join(timeout=2)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()

    def request(self, method, path, payload=None, *, token=True, host=None,
                origin=None, extra=()):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not False:
            connection.putheader("Host", host or f"127.0.0.1:{self.port}")
        if token is not False:
            connection.putheader("X-Moyu-Desktop-Token", self.token if token is True else token)
        if origin is not None:
            connection.putheader("Origin", origin)
        for key, value in extra:
            connection.putheader(key, value)
        raw = json.dumps(payload).encode("utf-8") if payload is not None else b""
        if payload is not None:
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(raw)))
        connection.endheaders(raw)
        response = connection.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        connection.close()
        return result

    def json_request(self, method, path, payload=None, **kwargs):
        status, raw, _ = self.request(method, path, payload, **kwargs)
        return status, json.loads(raw)


class DesktopBackendFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        UnexpectedProvider.calls = 0
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), UnexpectedProvider)
        cls.provider.daemon_threads = True
        cls.provider_thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.provider_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.provider_thread.join(timeout=2)
        if UnexpectedProvider.calls:
            raise AssertionError("Desktop fixtures unexpectedly called a model endpoint")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="moyu-desktop-fixture-")
        self.root = Path(self.temporary.name)
        self.backend = Backend(self.root, self.provider.server_port)
        self.backend.start()

    def tearDown(self):
        self.backend.stop()
        outputs = "".join(self.backend.stdout + self.backend.stderr)
        self.assertFalse(self.backend.token in outputs, "Desktop output contained its private token")
        self.assertNotIn("fixture-inherited-key", outputs)
        self.assertNotIn(BODY, outputs)
        self.assertEqual(len(self.backend.stdout), 1)
        self.assertEqual(UnexpectedProvider.calls, 0)
        self.temporary.cleanup()

    def test_health_is_private_and_minimal(self):
        status, data = self.backend.json_request("GET", "/api/desktop/health")
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ok": True})
        self.assertTrue((self.backend.data / "moyu.sqlite3").is_file())
        self.assertTrue((self.backend.data / "credentials").is_dir())
        self.assertFalse((self.root / "inherited.sqlite3").exists())
        self.assertFalse((self.root / "inherited-credentials").exists())
        with sqlite3.connect(self.backend.data / "moyu.sqlite3") as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 11)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        if os.name == "posix":
            self.assertEqual(self.backend.data.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.backend.data / "moyu.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_all_static_resources_match_source(self):
        for name in STATIC_FILES:
            with self.subTest(resource=name):
                status, raw, headers = self.backend.request("GET", "/" + name)
                self.assertEqual(status, 200)
                self.assertEqual(hashlib.sha256(raw).digest(),
                                 hashlib.sha256((ROOT / name).read_bytes()).digest())
                self.assertIn("content-type", {name.lower() for name in headers})
        status, raw, _ = self.backend.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(raw, (ROOT / "index.html").read_bytes())
        status, raw, _ = self.backend.request("HEAD", "/index.html")
        self.assertEqual((status, raw), (200, b""))

    def test_missing_wrong_and_duplicate_tokens_are_rejected(self):
        for token in (False, "wrong-desktop-token", "\u00e9" * 32):
            with self.subTest(kind=type(token).__name__):
                status, _, _ = self.backend.request("GET", "/api/desktop/health", token=token)
                self.assertEqual(status, 401)
        for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                status, _, _ = self.backend.request(method, "/", token=False)
                self.assertEqual(status, 401)
        status, _, _ = self.backend.request(
            "GET", "/api/desktop/health",
            extra=(("X-Moyu-Desktop-Token", self.backend.token),),
        )
        self.assertEqual(status, 401)

    def test_host_must_match_bound_loopback_port(self):
        for host in (False, "localhost:" + str(self.backend.port), "127.0.0.1",
                     "127.0.0.1:1", "attacker.invalid:" + str(self.backend.port)):
            with self.subTest(host_kind=type(host).__name__):
                status, _, _ = self.backend.request("GET", "/api/desktop/health", host=host)
                self.assertEqual(status, 403)
        status, _, _ = self.backend.request(
            "GET", "/api/desktop/health",
            extra=(("Host", f"127.0.0.1:{self.backend.port}"),),
        )
        self.assertEqual(status, 403)

    def test_origin_and_referer_are_same_origin(self):
        expected = f"http://127.0.0.1:{self.backend.port}"
        for origin in ("null", "https://attacker.invalid", "moyu://app",
                       "http://localhost:" + str(self.backend.port), expected + "/"):
            with self.subTest(origin=origin.split(":")[0]):
                status, _, _ = self.backend.request("GET", "/api/desktop/health", origin=origin)
                self.assertEqual(status, 403)
        status, data = self.backend.json_request(
            "GET", "/api/desktop/health", origin=expected,
            extra=(("Referer", expected + "/index.html"),),
        )
        self.assertEqual((status, data), (200, {"ok": True}))
        for extra in (
            (("Origin", expected), ("Origin", expected)),
            (("Referer", "https://attacker.invalid"),),
            (("Referer", expected + "/"), ("Referer", expected + "/")),
        ):
            status, _, _ = self.backend.request("GET", "/api/desktop/health", extra=extra)
            self.assertEqual(status, 403)
        status, _, _ = self.backend.request(
            "GET", "http://attacker.invalid/api/desktop/health",
        )
        self.assertEqual(status, 403)

    def test_private_files_are_hidden_for_get_and_head(self):
        forbidden = (
            "/moyu.sqlite3", "/credentials/", "/credentials/key.json", "/server.py",
            "/desktop/backend.py", "/docs/schema-v1.sql", "/.git/config",
            "/_internal/server.py", "/%2e%2e/server.py", "/docs/",
        )
        for method in ("GET", "HEAD"):
            for path in forbidden:
                with self.subTest(method=method, path=path):
                    status, raw, _ = self.backend.request(method, path)
                    self.assertEqual(status, 404)
                    if method == "HEAD":
                        self.assertEqual(raw, b"")

    def test_unauthorized_write_cannot_change_manuscript(self):
        status, before = self.backend.json_request("GET", "/api/state")
        self.assertEqual(status, 200)
        chapter = before["chapters"][0]
        status, _, _ = self.backend.request(
            "PATCH", "/api/chapters/" + chapter["id"], {"body": BODY}, token=False,
        )
        self.assertEqual(status, 401)
        status, after = self.backend.json_request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(before, after)

    def test_saved_manuscript_and_graph_survive_restart(self):
        status, state = self.backend.json_request("GET", "/api/state")
        self.assertEqual(status, 200)
        chapter_id = state["chapters"][0]["id"]
        status, saved = self.backend.json_request(
            "PATCH", "/api/chapters/" + chapter_id, {"body": BODY},
            origin=f"http://127.0.0.1:{self.backend.port}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["body"], BODY)
        status, planned = self.backend.json_request("POST", "/api/writing-agent", {
            "request_id": "desktop-fixture-plan", "chapter_id": chapter_id,
            "query": "Continue this scene.", "mode": "continue",
        })
        self.assertEqual(status, 200)
        session = planned["session"]
        self.assertEqual(session["status"], "awaiting_authorization")
        self.assertEqual(session["calls_used"], 0)
        self.assertTrue(session["checkpoint_id"])
        self.backend.stop()
        old_token = self.backend.token
        self.backend.token = secrets.token_urlsafe(40)
        self.backend.environment["MOYU_DESKTOP_TOKEN"] = self.backend.token
        self.backend.start()
        status, _, _ = self.backend.request("GET", "/api/desktop/health", token=old_token)
        self.assertEqual(status, 401)
        status, reopened = self.backend.json_request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(next(c["body"] for c in reopened["chapters"]
                              if c["id"] == chapter_id), BODY)
        status, sessions = self.backend.json_request(
            "GET", "/api/writing-agent?session_id=" + session["id"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(sessions["sessions"][0]["id"], session["id"])
        self.assertEqual(sessions["sessions"][0]["checkpoint_id"], session["checkpoint_id"])
        self.assertEqual(sessions["sessions"][0]["calls_used"], 0)

    def test_inherited_model_settings_are_cleared(self):
        for path in ("/api/ai/settings", "/api/agent-chat/settings"):
            status, value = self.backend.json_request("GET", path)
            self.assertEqual(status, 200)
            self.assertFalse(value["settings"]["configured"])
            self.assertFalse(value["settings"]["api_key_configured"])
            self.assertNotIn("fixture-inherited-model", json.dumps(value))
            self.assertNotIn("fixture-inherited-key", json.dumps(value))
        self.assertFalse((self.root / "home" / ".moyu").exists())

    def test_verified_model_library_assignment_and_preview_use_desktop_endpoints(self):
        provider = ThreadingHTTPServer(("127.0.0.1", 0), GoodProvider)
        provider.daemon_threads = True
        provider.calls = []
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, timeout=2)
        self.addCleanup(provider.server_close)
        self.addCleanup(provider.shutdown)
        key = "desktop-library-synthetic-" + secrets.token_hex(12)
        model = "desktop-library-model"
        base_url = f"http://127.0.0.1:{provider.server_port}/v1"
        receipt = None

        def assert_public(value, *, probe_response=False):
            serialized = json.dumps(value)
            self.assertFalse(key in serialized, "Desktop response included a private credential")
            if receipt and not probe_response:
                self.assertFalse(receipt in serialized,
                                 "Desktop response included a consumed verification token")
            pending = [value]
            while pending:
                item = pending.pop()
                if isinstance(item, dict):
                    self.assertNotIn("api_key", item)
                    if not probe_response:
                        self.assertNotIn("verification_token", item)
                    pending.extend(item.values())
                elif isinstance(item, list):
                    pending.extend(item)

        status, value = self.backend.json_request(
            "POST", "/api/ai/connections", {"name": "untested connection"},
        )
        self.assertEqual(status, 400)
        assert_public(value)
        candidate = {"name": "Desktop verified model", "base_url": base_url,
                     "model": model, "api_key": key}
        status, value = self.backend.json_request(
            "POST", "/api/ai/connections/probe", candidate, token=False,
        )
        self.assertEqual(status, 401)
        self.assertEqual(provider.calls, [])
        status, probe = self.backend.json_request(
            "POST", "/api/ai/connections/probe", candidate,
        )
        self.assertEqual(status, 200)
        self.assertTrue(probe["probe"]["success"])
        receipt = probe["verification_token"]
        self.assertTrue(receipt)
        assert_public(probe, probe_response=True)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["path"], "/v1/chat/completions")
        self.assertEqual(provider.calls[0]["payload"]["model"], model)
        self.assertTrue(provider.calls[0]["authorization"] == "Bearer " + key,
                        "Desktop probe used the wrong private credential")

        status, library = self.backend.json_request("GET", "/api/ai/connections")
        self.assertEqual(status, 200)
        self.assertEqual(library["connections"], [])
        assert_public(library)
        status, value = self.backend.json_request(
            "POST", "/api/ai/connections", {"verification_token": receipt}, token=False,
        )
        self.assertEqual(status, 401)
        assert_public(value)
        status, saved = self.backend.json_request(
            "POST", "/api/ai/connections", {"verification_token": receipt},
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["connection"]["verified"])
        identifier = saved["connection"]["id"]
        assert_public(saved)
        status, unassigned = self.backend.json_request("GET", "/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertFalse(unassigned["settings"]["configured"])
        self.assertFalse(unassigned["settings"]["connection_workflow"])
        self.assertEqual(unassigned["settings"]["default_connection_id"], "")
        self.assertEqual(unassigned["settings"]["task_connections"], {})
        assert_public(unassigned)
        status, rejected = self.backend.json_request("PUT", "/api/ai/settings", {
            "task_connections": {"continue": "desktop-untested-connection"},
        })
        self.assertEqual(status, 400)
        assert_public(rejected)
        status, unchanged = self.backend.json_request("GET", "/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertEqual(unchanged, unassigned)

        status, assigned = self.backend.json_request("PUT", "/api/ai/settings", {
            "default_connection_id": "", "task_connections": {"continue": identifier},
            "task_parameters": {"continue": {"temperature": 0.25, "max_tokens": 640}},
        })
        self.assertEqual(status, 200)
        self.assertTrue(assigned["settings"]["connection_workflow"])
        self.assertEqual(assigned["settings"]["task_connections"], {"continue": identifier})
        assert_public(assigned)
        status, preview = self.backend.json_request("POST", "/api/ai/prompt-preview", {
            "task": "continue", "instruction": "Continue the synthetic scene.",
        })
        self.assertEqual(status, 200)
        self.assertEqual(preview["request"]["model"], model)
        self.assertEqual(preview["request"]["temperature"], 0.25)
        self.assertEqual(preview["request"]["max_tokens"], 640)
        self.assertEqual(preview["request_audit"]["endpoint"], base_url + "/chat/completions")
        assert_public(preview)
        for path in ("/api/ai/connections", "/api/ai/settings", "/api/state"):
            status, value = self.backend.json_request("GET", path)
            self.assertEqual(status, 200)
            assert_public(value)
        self.assertEqual(len(provider.calls), 1, "Saving, assigning, or preview sent a model request")
        self.assertFalse(key.encode() in (self.backend.data / "moyu.sqlite3").read_bytes(),
                         "Desktop database included a private credential")
        self.assertFalse(receipt.encode() in (self.backend.data / "moyu.sqlite3").read_bytes(),
                         "Desktop database included a plaintext verification token")
        self.backend.stop()
        outputs = "".join(self.backend.stdout + self.backend.stderr)
        self.assertFalse(any(secret in outputs for secret in (key, receipt)),
                         "Desktop output included a private credential or verification token")

    def test_private_credentials_survive_restart_and_stay_out_of_backup(self):
        key = "desktop-fixture-secret-" + secrets.token_hex(12)
        status, result = self.backend.json_request("PUT", "/api/ai/settings", {
            "base_url": f"http://127.0.0.1:{self.provider.server_port}/v1",
            "model": "desktop-fixture-model", "api_key": key,
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["settings"]["api_key_configured"])
        self.assertFalse(key in json.dumps(result), "Settings returned a private credential")
        credentials = list((self.backend.data / "credentials").glob("*.json"))
        self.assertEqual(len(credentials), 1)
        self.assertEqual(json.loads(credentials[0].read_text(encoding="utf-8"))["api_key"], key)
        if os.name == "posix":
            self.assertEqual(credentials[0].stat().st_mode & 0o777, 0o600)
        self.assertFalse(key.encode() in (self.backend.data / "moyu.sqlite3").read_bytes(),
                         "Database included a private credential")
        self.backend.stop()
        self.assertFalse(key in "".join(self.backend.stdout + self.backend.stderr),
                         "Desktop output included a private credential")
        self.backend.start()
        status, result = self.backend.json_request("GET", "/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertTrue(result["settings"]["api_key_configured"])
        self.assertFalse(key in json.dumps(result), "Settings returned a private credential")
        status, backup = self.backend.json_request("POST", "/api/backups", {})
        self.assertEqual(status, 201)
        status, raw, _ = self.backend.request(
            "GET", "/api/backups/" + backup["id"] + "/download",
        )
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in archive.namelist():
                self.assertNotIn("credentials", name.lower())
                self.assertFalse(key.encode() in archive.read(name),
                                 "Backup included a private credential")
        self.backend.stop()
        self.assertFalse(key in "".join(self.backend.stdout + self.backend.stderr),
                         "Desktop output included a private credential")

    def test_parent_pipe_eof_stops_backend(self):
        self.backend.process.stdin.close()
        self.assertEqual(self.backend.process.wait(timeout=10), 0)
        self.assertTrue((self.backend.data / "moyu.sqlite3").is_file())

    def test_credential_file_validation_stays_strict(self):
        status, _ = self.backend.json_request("PUT", "/api/ai/settings", {
            "base_url": f"http://127.0.0.1:{self.provider.server_port}/v1",
            "model": "desktop-fixture-model", "api_key": "synthetic-validation-value",
        })
        self.assertEqual(status, 200)
        credential = next((self.backend.data / "credentials").glob("*.json"))
        original = credential.read_bytes()
        for invalid in (b'{"api_key":7}', b"x" * 20_001):
            credential.write_bytes(invalid)
            status, _ = self.backend.json_request("GET", "/api/ai/settings")
            self.assertEqual(status, 500)
        credential.write_bytes(original)
        if os.name == "posix":
            credential.chmod(0o644)
            status, _ = self.backend.json_request("GET", "/api/ai/settings")
            self.assertEqual(status, 500)
            credential.chmod(0o600)
            real_file = self.root / "synthetic-credential.json"
            credential.rename(real_file)
            credential.symlink_to(real_file)
            status, _ = self.backend.json_request("GET", "/api/ai/settings")
            self.assertEqual(status, 500)
            credential.unlink()
            real_file.rename(credential)
        status, result = self.backend.json_request("GET", "/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertTrue(result["settings"]["api_key_configured"])

    @unittest.skipUnless(os.name == "posix", "POSIX symlink fixture")
    def test_symlinked_private_storage_is_rejected_before_startup(self):
        linked_data = self.root / "linked-data"
        linked_data.symlink_to(self.backend.data, target_is_directory=True)
        alternate = self.root / "alternate-data"
        alternate.mkdir()
        marker = self.root / "synthetic-marker"
        marker.write_bytes(b"synthetic marker, never a database")
        (alternate / "moyu.sqlite3").symlink_to(marker)
        for directory in (linked_data, alternate):
            result = subprocess.run(
                BACKEND_COMMAND, cwd=self.root,
                env={**self.backend.environment, "MOYU_DESKTOP_DATA_DIR": str(directory)},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(json.loads(result.stderr), {
                "type": "moyu-error", "code": "configuration_invalid",
            })
        self.assertEqual(marker.read_bytes(), b"synthetic marker, never a database")

    def test_invalid_configuration_exits_without_sensitive_output(self):
        changes = (
            {"MOYU_DESKTOP_DATA_DIR": ""},
            {"MOYU_DESKTOP_DATA_DIR": "relative-data"},
            {"MOYU_DESKTOP_TOKEN": "short"},
            {"MOYU_DESKTOP_TOKEN": "\u00e9" * 32},
            {"MOYU_DESKTOP_PORT": "-1"},
            {"MOYU_DESKTOP_PORT": "65536"},
        )
        for patch in changes:
            with self.subTest(fields=list(patch)):
                result = subprocess.run(
                    BACKEND_COMMAND, cwd=self.root,
                    env={**self.backend.environment, **patch},
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=15,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(json.loads(result.stderr), {
                    "type": "moyu-error", "code": "configuration_invalid",
                })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--backend", type=Path)
    args, remaining = parser.parse_known_args()
    if args.backend:
        executable = args.backend.resolve(strict=True)
        BACKEND_COMMAND = [str(executable)]
    unittest.main(argv=[sys.argv[0], *remaining])
