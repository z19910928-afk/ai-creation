"""Local gateway compatibility checks without external credentials or traffic."""
from __future__ import annotations

import io
import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.moyu_provider import (
    ProviderError, list_models, normalize_json_completion, parse_sse_completion,
    prepare_chat_request,
)


SECRET = "synthetic-compatibility-credential"


class ModelsHandler(BaseHTTPRequestHandler):
    paths: list[str] = []
    authorizations: list[str | None] = []

    def do_GET(self):
        self.paths.append(self.path)
        self.authorizations.append(self.headers.get("Authorization"))
        attempt = self.paths.count(self.path)
        if self.path.startswith("/retry-budget/"):
            time.sleep(0.15 if attempt == 1 else 0.2)
        if (self.path.startswith("/disconnect/")
                or self.path.startswith(("/recover/", "/retry-budget/")) and attempt == 1):
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if self.path.startswith("/body-disconnect/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b'{"data":[')
            self.wfile.flush()
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if self.path.startswith("/slow-"):
            headers = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"
            body = b'{"data":[{"id":"fixture"}]}'
            self.close_connection = True
            try:
                if self.path.startswith("/slow-headers/"):
                    for piece in headers:
                        self.wfile.write(bytes([piece]))
                        self.wfile.flush()
                        time.sleep(0.025)
                    self.wfile.write(body)
                else:
                    self.wfile.write(headers)
                    for piece in body:
                        self.wfile.write(bytes([piece]))
                        self.wfile.flush()
                        time.sleep(0.025)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        status, content_type = 200, "application/json"
        if self.path.startswith("/unauthorized/"):
            status, body = 401, {"error": SECRET}
        elif self.path.startswith("/forbidden/"):
            status, body = 403, {"error": SECRET}
        elif self.path.startswith("/rate-limited/"):
            status, body = 429, {"error": SECRET}
        elif self.path.startswith("/redirect/"):
            status, body = 302, {"error": SECRET}
        elif self.path.startswith("/invalid/"):
            body = {"data": [{"id": 123}]}
        elif self.path.startswith("/malformed/"):
            body = {"models": ["fixture"]}
        elif self.path.startswith("/secret/"):
            body = {"data": [{"id": SECRET}]}
        elif self.path.startswith("/html/"):
            content_type, body = "text/html", {"error": SECRET}
        elif self.path.startswith("/empty/"):
            body = {"data": []}
        elif self.path.startswith("/large/"):
            body = {"data": [{"id": str(index)} for index in range(1001)]}
        else:
            body = {"data": [{"id": "Zeta"}, {"id": "alpha"}, {"id": "alpha"}]}
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Location", "/should-not-follow/models")
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


class TLSModelsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, context):
        self.context = context
        self.connection_attempts = 0
        self.drop_next_handshake = False
        super().__init__(address, ModelsHandler)

    def get_request(self):
        request, address = super().get_request()
        self.connection_attempts += 1
        if self.drop_next_handshake:
            self.drop_next_handshake = False
            request.shutdown(socket.SHUT_RDWR)
            request.close()
            raise ConnectionAbortedError("synthetic TLS handshake interruption")
        return self.context.wrap_socket(request, server_side=True), address


def completion(*, top_model=None, choice_model=None):
    result = {
        "choices": [{"message": {"content": "连接正常"}, "finish_reason": "stop"}],
    }
    if top_model is not None:
        result["model"] = top_model
    if choice_model is not None:
        result["choices"][0]["model"] = choice_model
    return result


class ProviderCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
        cls.thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.provider.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.thread.join(timeout=3)

    def test_base_and_copied_endpoint_use_same_chat_url(self):
        for suffix in ("/v1", "/v1/", "/v1/chat/completions", "/v1/chat/completions/"):
            with self.subTest(suffix=suffix):
                prepared = prepare_chat_request(
                    {"base_url": self.base + suffix, "model": "fixture"},
                    [{"role": "user", "content": "test"}],
                )
                self.assertEqual(prepared["endpoint"], self.base + "/v1/chat/completions")
                self.assertEqual(prepared["path"], "/v1/chat/completions")

    def test_usage_extension_is_opt_in_for_streaming(self):
        for enabled in (False, True):
            config = {"base_url": self.base, "model": "fixture", "include_usage": enabled}
            prepared = prepare_chat_request(config, [{"role": "user", "content": "test"}], stream=True)
            self.assertEqual("stream_options" in prepared["payload"], enabled)
        default = prepare_chat_request(
            {"base_url": self.base, "model": "fixture"},
            [{"role": "user", "content": "test"}], stream=True,
        )
        self.assertNotIn("stream_options", default["payload"])

    def test_output_token_parameter_reaches_exact_payload(self):
        prepared = prepare_chat_request(
            {"base_url": self.base, "model": "fixture", "output_token_parameter": "max_completion_tokens"},
            [{"role": "user", "content": "test"}], max_tokens=512,
        )
        self.assertNotIn("max_tokens", prepared["payload"])
        self.assertEqual(prepared["payload"]["max_completion_tokens"], 512)
        self.assertEqual(prepared["request_audit"]["payload"], prepared["payload"])

    def test_model_is_read_from_top_or_choice_with_missing_kept_unknown(self):
        for top, choice in (("fixture", None), (None, "fixture"), ("fixture", "fixture")):
            with self.subTest(top=top, choice=choice):
                result = normalize_json_completion(completion(top_model=top, choice_model=choice))
                self.assertEqual(result["returned_model"], "fixture")
        self.assertIsNone(normalize_json_completion(completion())["returned_model"])

    def test_model_conflict_and_invalid_values_are_rejected(self):
        for top, choice in (("first", "second"), (False, None), (None, 3), ("bad\nname", None)):
            with self.subTest(top=top, choice=choice):
                with self.assertRaises(ProviderError):
                    normalize_json_completion(completion(top_model=top, choice_model=choice))

    def test_choice_model_is_checked_across_stream_events(self):
        chunks = []
        for model, finish in (("first", None), ("second", "stop")):
            value = {"choices": [{"model": model, "delta": {"content": "text"}, "finish_reason": finish}]}
            chunks.append("data: " + json.dumps(value) + "\n\n")
        with self.assertRaisesRegex(ProviderError, "模型名称发生改变"):
            parse_sse_completion(io.BytesIO(("".join(chunks) + "data: [DONE]\n\n").encode()))

    def test_models_list_uses_get_and_supports_copied_chat_endpoint(self):
        for suffix in ("/v1", "/v1/chat/completions"):
            result = list_models({"base_url": self.base + suffix, "api_key": SECRET})
            self.assertEqual(result, {"models": ["alpha", "Zeta"], "endpoint": self.base + "/v1/models"})
            self.assertEqual(ModelsHandler.paths[-1], "/v1/models")
            self.assertEqual(ModelsHandler.authorizations[-1], "Bearer " + SECRET)

    def test_empty_models_list_is_a_valid_empty_result(self):
        self.assertEqual(list_models({"base_url": self.base + "/empty"})["models"], [])

    def test_models_errors_do_not_echo_upstream_credentials(self):
        for case in ("unauthorized", "invalid", "malformed", "html", "large"):
            with self.subTest(case=case):
                with self.assertRaises(ProviderError) as raised:
                    list_models({"base_url": self.base + "/" + case, "api_key": SECRET})
                self.assertNotIn(SECRET, str(raised.exception))
        result = list_models({"base_url": self.base + "/secret", "api_key": SECRET})
        self.assertNotIn(SECRET, json.dumps(result))

    def test_models_redirect_does_not_forward_the_authorization(self):
        count = len(ModelsHandler.paths)
        with self.assertRaises(ProviderError):
            list_models({"base_url": self.base + "/redirect", "api_key": SECRET})
        self.assertEqual(ModelsHandler.paths[count:], ["/redirect/models"])

    def test_models_total_deadline_stops_slow_headers_and_detached_body(self):
        for case in ("slow-headers", "slow-body"):
            with self.subTest(case=case):
                started = time.monotonic()
                with self.assertRaises(ProviderError) as raised:
                    list_models({"base_url": self.base + "/" + case,
                                 "connect_timeout": 0.5, "total_timeout": 0.15})
                self.assertEqual(raised.exception.code, "models_timeout")
                self.assertLess(time.monotonic() - started, 0.5)

    def test_models_connection_refusal_is_distinct_and_does_not_leak_credentials(self):
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
        with self.assertRaises(ProviderError) as raised:
            list_models({"base_url": f"http://127.0.0.1:{port}",
                         "api_key": SECRET, "connect_timeout": 1, "total_timeout": 2})
        self.assertEqual(raised.exception.code, "models_connection_refused")
        self.assertNotIn(SECRET, str(raised.exception))

    def test_models_peer_disconnect_reconnects_at_most_once(self):
        count = len(ModelsHandler.paths)
        with self.assertRaises(ProviderError) as raised:
            list_models({"base_url": self.base + "/disconnect", "api_key": SECRET})
        self.assertEqual(raised.exception.code, "models_connection_reset")
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(ModelsHandler.paths[count:], ["/disconnect/models"] * 2)

    def test_models_first_disconnect_recovers_with_same_get_and_authorization(self):
        count = len(ModelsHandler.paths)
        result = list_models({"base_url": self.base + "/recover", "api_key": SECRET})
        self.assertEqual(result["models"], ["alpha", "Zeta"])
        self.assertEqual(ModelsHandler.paths[count:], ["/recover/models"] * 2)
        self.assertEqual(ModelsHandler.authorizations[count:], ["Bearer " + SECRET] * 2)

    def test_models_reconnect_keeps_one_total_deadline(self):
        count = len(ModelsHandler.paths)
        started = time.monotonic()
        with self.assertRaises(ProviderError) as raised:
            list_models({"base_url": self.base + "/retry-budget", "api_key": SECRET,
                         "connect_timeout": 1, "total_timeout": 0.25})
        self.assertEqual(raised.exception.code, "models_timeout")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(ModelsHandler.paths[count:], ["/retry-budget/models"] * 2)

    def test_models_deadline_wins_before_abort_timer_is_scheduled(self):
        for phase in ("connect", "response", "error"):
            with self.subTest(phase=phase):
                clock = {"now": 0.0}

                def connect(_connection):
                    if phase in {"connect", "error"}:
                        clock["now"] = 0.3
                    if phase == "error":
                        raise ConnectionResetError(SECRET)

                def response(_connection):
                    clock["now"] = 0.3
                    return io.BytesIO()

                with patch("backend.moyu_provider.time.monotonic", side_effect=lambda: clock["now"]), patch(
                        "backend.moyu_provider.threading.Timer"), patch(
                        "backend.moyu_provider.http.client.HTTPConnection.connect", connect), patch(
                        "backend.moyu_provider.http.client.HTTPConnection.request") as request, patch(
                        "backend.moyu_provider.http.client.HTTPConnection.getresponse", response):
                    with self.assertRaises(ProviderError) as raised:
                        list_models({"base_url": self.base, "api_key": SECRET,
                                     "connect_timeout": 1, "total_timeout": 0.25})
                self.assertEqual(raised.exception.code, "models_timeout")
                self.assertNotIn(SECRET, str(raised.exception))
                self.assertEqual(request.call_count, 1 if phase == "response" else 0)

    def test_models_http_errors_and_invalid_body_do_not_reconnect(self):
        for case in ("unauthorized", "forbidden", "rate-limited", "redirect",
                     "invalid", "malformed", "html", "body-disconnect"):
            with self.subTest(case=case):
                count = len(ModelsHandler.paths)
                with self.assertRaises(ProviderError):
                    list_models({"base_url": self.base + "/" + case, "api_key": SECRET})
                self.assertEqual(ModelsHandler.paths[count:], ["/" + case + "/models"])

    def test_models_network_failures_use_safe_specific_messages(self):
        cases = (
            (ssl.SSLCertVerificationError(1, SECRET), "models_certificate_error"),
            (ssl.SSLError(1, SECRET), "models_tls_error"),
            (ssl.SSLEOFError(8, SECRET), "models_tls_error"),
            (socket.gaierror(socket.EAI_NONAME, SECRET), "models_dns_error"),
            (ConnectionRefusedError(111, SECRET), "models_connection_refused"),
            (ConnectionResetError(104, SECRET), "models_connection_reset"),
            (ConnectionAbortedError(103, SECRET), "models_connection_reset"),
            (BrokenPipeError(32, SECRET), "models_connection_reset"),
            (http.client.RemoteDisconnected(SECRET), "models_connection_reset"),
            (TimeoutError(SECRET), "models_timeout"),
            (OSError(SECRET), "models_connection_interrupted"),
            (http.client.BadStatusLine(SECRET), "models_connection_interrupted"),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__), patch(
                    "backend.moyu_provider.http.client.HTTPConnection.connect",
                    side_effect=error) as connect:
                with self.assertRaises(ProviderError) as raised:
                    list_models({"base_url": self.base, "api_key": SECRET})
                self.assertEqual(raised.exception.code, expected)
                self.assertNotIn(SECRET, str(raised.exception))
                self.assertIsNone(raised.exception.http_status)
                self.assertTrue(raised.exception.__suppress_context__)
                self.assertEqual(connect.call_count, 2 if isinstance(
                    error, (ConnectionResetError, ssl.SSLEOFError)) else 1)

    def test_models_tls_context_failure_uses_safe_certificate_message(self):
        with patch("backend.moyu_provider.ssl.create_default_context",
                   side_effect=ssl.SSLCertVerificationError(1, SECRET)):
            with self.assertRaises(ProviderError) as raised:
                list_models({"base_url": "https://localhost", "api_key": SECRET})
        self.assertEqual(raised.exception.code, "models_certificate_error")
        self.assertNotIn(SECRET, str(raised.exception))

    def test_models_plain_http_at_https_address_reports_tls_failure(self):
        with self.assertRaises(ProviderError) as raised:
            list_models({"base_url": self.base.replace("http:", "https:"),
                         "api_key": SECRET, "connect_timeout": 1, "total_timeout": 2})
        self.assertEqual(raised.exception.code, "models_tls_error")
        self.assertNotIn(SECRET, str(raised.exception))

    def test_models_endpoint_rejects_insecure_or_credential_bearing_urls(self):
        for base in ("http://remote.example/v1", "https://user:password@example.com/v1",
                     "https://example.com/v1?key=secret", "https://example.com/v1#fragment",
                     "http://127.0.0.1:0/v1", "https://[invalid]/v1"):
            with self.subTest(base=base):
                with self.assertRaises(ProviderError):
                    list_models({"base_url": base, "api_key": SECRET})


class ModelsTLSCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = shutil.which("openssl")
        if executable is None:
            raise unittest.SkipTest("OpenSSL is required to generate an isolated localhost certificate")
        cls.temporary = tempfile.TemporaryDirectory(prefix="moyu-models-tls-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.certificate = cls.directory / "localhost-cert.pem"
        private_key = cls.directory / "localhost-key.pem"
        configuration = cls.directory / "localhost.cnf"
        configuration.write_text(
            "[req]\nprompt=no\ndistinguished_name=subject\nx509_extensions=extensions\n"
            "[subject]\nCN=localhost\n"
            "[extensions]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:TRUE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n",
            encoding="ascii",
        )
        subprocess.run(
            [executable, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
             "-config", str(configuration), "-keyout", str(private_key),
             "-out", str(cls.certificate)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cls.certificate), str(private_key))
        cls.provider = TLSModelsServer(("127.0.0.1", 0), context)
        cls.thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        cls.base = f"https://127.0.0.1:{cls.provider.server_port}/v1"
        cls.empty_ca_directory = cls.directory / "empty-ca"
        cls.empty_ca_directory.mkdir()

    @classmethod
    def stop_server(cls):
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.thread.join(timeout=3)

    def test_trusted_local_tls_uses_certificate_and_hostname_verification(self):
        with patch.dict(os.environ, {
            "SSL_CERT_FILE": str(self.certificate),
            "SSL_CERT_DIR": str(self.empty_ca_directory),
        }):
            context = ssl.create_default_context()
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            result = list_models({"base_url": self.base, "api_key": SECRET})
        self.assertEqual(result, {"models": ["alpha", "Zeta"], "endpoint": self.base + "/models"})
        self.assertEqual(ModelsHandler.authorizations[-1], "Bearer " + SECRET)

    def test_untrusted_local_tls_fails_before_sending_credentials(self):
        count = len(ModelsHandler.paths)
        connections = self.provider.connection_attempts
        with patch.dict(os.environ, {
            "SSL_CERT_FILE": str(self.directory / "not-a-trusted-root.pem"),
            "SSL_CERT_DIR": str(self.empty_ca_directory),
        }):
            with self.assertRaises(ProviderError) as raised:
                list_models({"base_url": self.base, "api_key": SECRET})
        self.assertEqual(raised.exception.code, "models_certificate_error")
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(len(ModelsHandler.paths), count)
        self.assertEqual(self.provider.connection_attempts - connections, 1)

    def test_tls_eof_before_response_reconnects_with_certificate_checks(self):
        connections = self.provider.connection_attempts
        self.provider.drop_next_handshake = True
        with patch.dict(os.environ, {
            "SSL_CERT_FILE": str(self.certificate),
            "SSL_CERT_DIR": str(self.empty_ca_directory),
        }):
            result = list_models({"base_url": self.base, "api_key": SECRET})
        self.assertEqual(result["models"], ["alpha", "Zeta"])
        self.assertEqual(self.provider.connection_attempts - connections, 2)


if __name__ == "__main__":
    unittest.main()
