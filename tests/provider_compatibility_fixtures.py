"""Local gateway compatibility checks without external credentials or traffic."""
from __future__ import annotations

import io
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from moyu_provider import (
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
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


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

    def test_models_endpoint_rejects_insecure_or_credential_bearing_urls(self):
        for base in ("http://remote.example/v1", "https://user:password@example.com/v1",
                     "https://example.com/v1?key=secret", "https://example.com/v1#fragment",
                     "http://127.0.0.1:0/v1", "https://[invalid]/v1"):
            with self.subTest(base=base):
                with self.assertRaises(ProviderError):
                    list_models({"base_url": base, "api_key": SECRET})


if __name__ == "__main__":
    unittest.main()
