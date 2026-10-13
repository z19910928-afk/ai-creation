#!/usr/bin/env python3
"""Real local HTTP protocol regressions; no credential or paid provider needed."""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moyu_provider import (ProviderCanceled, ProviderError, call_chat_completion,
                           prepare_chat_request, reasoning_field_counts, usage_accounting)


USAGE_CASES = {
    "empty": {},
    "prompt-only": {"prompt_tokens": 7},
    "completion-only": {"completion_tokens": 9},
    "details-only": {"completion_tokens_details": {"reasoning_tokens": 9}},
    "components": {"prompt_tokens": 7, "completion_tokens": 9},
    "total-only": {"total_tokens": 16},
    "total-zero": {"total_tokens": 0},
    "conflict": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 12},
    "component-exceeds-total": {"prompt_tokens": 17, "total_tokens": 16},
    "bool": {"total_tokens": True, "completion_tokens": 9},
    "negative": {"total_tokens": -1, "completion_tokens": 9},
    "float": {"total_tokens": 16.0, "completion_tokens": 9},
}
REASONING_FIELDS = {
    "alias": {"reasoning": "私密思考全文不可保存"},
    "object": {"reasoning_content": {"text": "私密思考全文不可保存"}},
    "empty-object": {"reasoning": {}},
    "array": {"reasoning_content": []},
    "boolean": {"reasoning": False},
    "number": {"reasoning_content": 0},
    "null": {"reasoning_content": None, "reasoning": None},
    "empty": {"reasoning_content": "", "reasoning": " \n"},
    "both": {"reasoning_content": "私密思考全文不可保存", "reasoning": "第二个私密字段"},
}


def sse(content: str = "", finish: str | None = None, **extra: object) -> bytes:
    return ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": content},
                                               "finish_reason": finish}], **extra},
                                   ensure_ascii=False) + "\n\n").encode()


def completion(content: str = "安全正文", finish: str | None = "stop",
               **extra: object) -> bytes:
    return json.dumps({"model": "actual-fixture", "choices": [
        {"index": 0, "message": {"role": "assistant", "content": content},
         "finish_reason": finish}], **extra}, ensure_ascii=False).encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[dict] = []
    lock = threading.Lock()

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        case = self.path.split("/")[1]
        with self.lock:
            self.requests.append({"case": case, "payload": json.loads(raw), "raw": raw,
                                  "authorization": self.headers.get("Authorization")})
        if case == "redirect":
            self.send_response(302)
            self.send_header("Location", "/should-not-follow/chat/completions")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if case == "http-error":
            body = b'{"error":"fixture-sensitive-value"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        json_cases = {"json", "wrong-schema", "wrong-usage", "json-length", "json-unknown",
                      "size", "secret-json", "wrong-content", "nan", "usage-extra",
                      "delayed-headers", "delayed-json", "vendor-finish", "usage-secret-key",
                      "json-empty-finish", "reasoning-json", "reasoning-json-empty"}
        json_cases.update("usage-" + name for name in USAGE_CASES)
        json_cases.update("reasoning-variant-json-" + name for name in REASONING_FIELDS)
        if case == "delayed-headers":
            time.sleep(0.35)
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if case in json_cases else "text/event-stream")
        self.send_header("X-Request-Id", "provider-request-id")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            if case.startswith("usage-") and case[6:] in USAGE_CASES:
                self.wfile.write(completion(usage=USAGE_CASES[case[6:]]))
            elif case in {"json", "delayed-headers"}:
                self.wfile.write(completion())
            elif case == "delayed-json":
                time.sleep(0.35)
                self.wfile.write(completion())
            elif case == "wrong-schema":
                self.wfile.write(completion(json.dumps({"pov": [], "location": {}, "goal": 3,
                                                        "obstacle": None, "exit": False})))
            elif case == "wrong-usage":
                self.wfile.write(completion(usage={"prompt_tokens": "3"}))
            elif case == "usage-extra":
                self.wfile.write(completion(usage={"prompt_tokens": 3, "completion_tokens": 2,
                                                   "total_tokens": 5,
                                                   "vendor_note": "fixture-sensitive-value"}))
            elif case == "usage-secret-key":
                self.wfile.write(completion(usage={
                    "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
                    "fixture-sensitive-value": {"nested-fixture-sensitive-value": "metadata"},
                    "completion_tokens_details": {"reasoning_tokens": 1,
                                                  "detail-fixture-sensitive-value": 1}}))
            elif case == "json-length":
                self.wfile.write(completion(finish="length"))
            elif case == "json-unknown":
                self.wfile.write(completion(finish=None))
            elif case == "json-empty-finish":
                self.wfile.write(completion(finish=""))
            elif case in {"reasoning-json", "reasoning-json-empty"}:
                self.wfile.write(json.dumps({
                    "model": "actual-fixture", "choices": [{
                        "message": {"content": "铁", "reasoning_content":
                                    "私密思考全文不可保存" if case == "reasoning-json" else " \n"},
                        "finish_reason": "stop",
                    }], "usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
                }, ensure_ascii=False).encode())
            elif case.startswith("reasoning-variant-json-"):
                fields = REASONING_FIELDS[case[len("reasoning-variant-json-"):]]
                self.wfile.write(json.dumps({
                    "model": "actual-fixture", "choices": [{
                        "message": {"content": "只有真实正文", **fields},
                        "finish_reason": "stop",
                    }], "usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
                }, ensure_ascii=False).encode())
            elif case == "vendor-finish":
                self.wfile.write(completion(finish="vendor-fixture-sensitive-value"))
            elif case == "size":
                self.wfile.write(completion("x" * 3000))
            elif case == "secret-json":
                self.wfile.write(completion("前fixture-sensitive-value后"))
            elif case == "wrong-content":
                self.wfile.write(b'{"choices":[{"message":{"content":123},"finish_reason":"stop"}]}')
            elif case == "nan":
                self.wfile.write(b'{"choices":NaN}')
            elif case in {"slow", "cancel", "idle"}:
                self.wfile.write(sse("第一段", model="actual-fixture"))
                self.wfile.flush()
                time.sleep(0.9)
                self.wfile.write(sse("第二段", "stop") + b"data: [DONE]\n\n")
            elif case == "total":
                for _ in range(12):
                    self.wfile.write(b": alive\n\n")
                    self.wfile.flush()
                    time.sleep(0.05)
                self.wfile.write(sse("晚到", "stop") + b"data: [DONE]\n\n")
            elif case == "length":
                self.wfile.write(sse("被截断", "length") + b"data: [DONE]\n\n")
            elif case == "eof":
                self.wfile.write(sse("半段", "stop"))
            elif case == "unfinished-event":
                self.wfile.write(sse("完整第一片") + b'data: {"choices":[{"delta":{"content":"dangling"}}]}')
            elif case == "done-only":
                self.wfile.write(sse("没有结束原因") + b"data: [DONE]\n\n")
            elif case in {"empty-first-finish", "empty-only-finish", "empty-finish-eof"}:
                self.wfile.write(sse("", ""))
                self.wfile.write(sse("首段空标记后的正文", ""))
                if case == "empty-first-finish":
                    self.wfile.write(sse("", "stop"))
                if case != "empty-finish-eof":
                    self.wfile.write(b"data: [DONE]\n\n")
            elif case == "filter":
                self.wfile.write(sse("", "content_filter") + b"data: [DONE]\n\n")
            elif case == "refusal":
                self.wfile.write(b'data: {"choices":[{"delta":{"refusal":"No"},"finish_reason":"stop"}]}\n\n'
                                 b"data: [DONE]\n\n")
            elif case == "bad-event":
                self.wfile.write(b"data: not-json\n\n")
            elif case == "bad-utf8":
                self.wfile.write(b"data: \xff\n\n")
            elif case == "model-change":
                self.wfile.write(sse("甲", model="model-a") + sse("乙", "stop", model="model-b"))
            elif case == "stream-error":
                self.wfile.write(sse("首段") + b'event: error\ndata: {"error":{"message":"hidden"}}\n\n')
            elif case.startswith("usage-stream-"):
                self.wfile.write(sse("保留正文", model="actual-fixture"))
                mode = case[len("usage-stream-"):]
                usage_chunks = {
                    "merge": [{"prompt_tokens": 7}, {"completion_tokens": 9}, {}, {}],
                    "repeat": [{"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16}] * 3,
                    "regress": [{"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
                                {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}],
                    "conflict-then-correct": [
                        {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 12},
                        {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16}],
                    "partial-error": [{"completion_tokens": 9}],
                    "full-eof": [{"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16}],
                    "partial-eof": [{"completion_tokens": 9}],
                    "partial-cancel": [{"completion_tokens": 9}],
                    "partial-length": [{"completion_tokens": 9}],
                }[mode]
                for usage in usage_chunks:
                    self.wfile.write(("data: " + json.dumps(
                        {"choices": [], "usage": usage}) + "\n\n").encode())
                if mode == "partial-error":
                    self.wfile.write(b'event: error\ndata: {"error":{"message":"hidden"},'
                                     b'"usage":{"completion_tokens":11}}\n\n')
                elif mode == "partial-cancel":
                    self.wfile.write(sse("触发取消"))
                    self.wfile.flush()
                    time.sleep(0.2)
                elif mode not in {"full-eof", "partial-eof"}:
                    self.wfile.write(sse("", "length" if mode == "partial-length" else "stop")
                                     + b"data: [DONE]\n\n")
            elif case == "secret-stream":
                for text in ("前fixture-", "sensitive", "-value后"):
                    self.wfile.write(sse(text))
                    self.wfile.flush()
                self.wfile.write(sse("", "stop") + b"data: [DONE]\n\n")
            elif case.startswith("reasoning-variant-stream-"):
                name = case[len("reasoning-variant-stream-"):]
                count = 130 if name == "invalid-middle" else 2
                for index in range(count):
                    fields = ({"reasoning": {} if index == 113 else None}
                              if name == "invalid-middle" else REASONING_FIELDS[name])
                    self.wfile.write(("data: " + json.dumps({
                        "choices": [{"index": 0, "delta": fields, "finish_reason": None}],
                    }, ensure_ascii=False) + "\n\n").encode())
                self.wfile.write(sse("只有真实正文", "stop",
                                    usage={"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16})
                                 + b"data: [DONE]\n\n")
            elif case in {"reasoning-only", "reasoning-body", "reasoning-middle", "tail-failure"}:
                count = 130 if case in {"tail-failure", "reasoning-middle"} else 2
                for _ in range(count):
                    self.wfile.write(("data: " + json.dumps({
                        "choices": [{"index": 0, "delta": {
                            "reasoning_content": ("私密思考全文不可保存"
                                                  if case != "reasoning-middle" or _ == 113 else ""),
                            "fixture-sensitive-value": "不可保存的字段值"},
                            "finish_reason": None}]}, ensure_ascii=False) + "\n\n").encode())
                if case == "tail-failure":
                    self.wfile.write(sse("", "vendor-failure-at-tail"))
                else:
                    self.wfile.write(sse("只有真实正文" if case != "reasoning-only" else "", "stop",
                                        usage={"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16})
                                     + b"data: [DONE]\n\n")
            else:
                self.wfile.write(b": heartbeat\r\n\r\nid: event-one\r\nevent: message\r\n")
                self.wfile.write(sse("第一", model="actual-fixture"))
                self.wfile.write('data: {"choices":[{"delta":\n'
                                 'data: {"content":"二段"},"finish_reason":null}]}\n\n'.encode())
                self.wfile.write(sse("", "stop"))
                self.wfile.write(b'data: {"choices":[],"usage":{"prompt_tokens":11,'
                                 b'"completion_tokens":3,"total_tokens":14,'
                                 b'"prompt_tokens_details":{"cached_tokens":4},'
                                 b'"completion_tokens_details":{"reasoning_tokens":1}}}\n\n'
                                 b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args: object) -> None:
        return


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def config(self, case: str, **extra: object) -> dict:
        return {"base_url": self.base + "/" + case, "model": "requested-fixture",
                "api_key": "", "total_timeout": 5, **extra}

    def call(self, case: str, *, config: dict | None = None, **kwargs: object) -> dict:
        return call_chat_completion(config or self.config(case),
                                    [{"role": "user", "content": "续写测试"}], **kwargs)

    def test_explicit_thinking_object_is_sent_and_retained_in_audit(self) -> None:
        for mode in ("enabled", "disabled"):
            with self.subTest(mode=mode):
                result = self.call("json", parameters={"thinking": {"type": mode}})
                self.assertTrue(result["safe_to_apply"])
                self.assertEqual(Handler.requests[-1]["payload"]["thinking"], {"type": mode})
                self.assertEqual(result["request_audit"]["effective_parameters"]["thinking"],
                                 {"type": mode})
                self.assertNotIn("reasoning_effort", Handler.requests[-1]["payload"])

    def test_invalid_thinking_objects_and_efforts_are_rejected_before_dispatch(self) -> None:
        invalid = [None, False, "disabled", [], {}, {"type": None}, {"type": False},
                   {"type": []}, {"type": {}}, {"type": "Disabled"}, {"type": "auto"},
                   {"enabled": False}, {"type": "disabled", "budget_tokens": 3000}]
        before = len(Handler.requests)
        for thinking in invalid:
            with self.subTest(thinking=thinking), self.assertRaises(ProviderError) as caught:
                self.call("json", parameters={"thinking": thinking})
            self.assertEqual(caught.exception.code, "invalid_request")
            self.assertEqual(caught.exception.billing_state, "not_dispatched")
        for effort in (None, False, [], {}, 0):
            with self.subTest(effort=effort), self.assertRaises(ProviderError) as caught:
                self.call("json", parameters={"reasoning_effort": effort})
            self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(len(Handler.requests), before)

    def test_thinking_and_compatible_effort_must_agree_after_parameter_merge(self) -> None:
        before = len(Handler.requests)
        for mode, effort in (("enabled", "none"), ("disabled", "low"), ("disabled", "high")):
            with self.subTest(mode=mode, effort=effort), self.assertRaises(ProviderError):
                self.call("json", config=self.config(
                    "json", parameters={"thinking": {"type": mode}},
                    task_parameters={"continue": {"reasoning_effort": effort}}))
        self.assertEqual(len(Handler.requests), before)
        for mode, effort in (("disabled", "none"), ("enabled", "minimal"),
                             ("enabled", "low"), ("enabled", "high")):
            with self.subTest(mode=mode, effort=effort):
                prepared = prepare_chat_request(
                    self.config("json"), [{"role": "user", "content": "兼容参数"}],
                    parameters={"thinking": {"type": mode}, "reasoning_effort": effort})
                self.assertEqual(prepared["payload"]["thinking"], {"type": mode})
                self.assertEqual(prepared["payload"]["reasoning_effort"], effort)
        legacy = prepare_chat_request(
            self.config("json"), [{"role": "user", "content": "原有参数"}],
            parameters={"reasoning_effort": "none"})
        self.assertEqual(legacy["payload"]["reasoning_effort"], "none")
        self.assertNotIn("thinking", legacy["payload"])

    def test_frozen_request_preserves_thinking_when_current_configuration_changes(self) -> None:
        import server
        messages = [{"role": "system", "content": "只返回正文"},
                    {"role": "user", "content": "续写场景"}]
        for mode in ("enabled", "disabled"):
            with self.subTest(mode=mode):
                prepared = prepare_chat_request(
                    self.config("normal"), messages, max_tokens=3000, stream=True,
                    parameters={"temperature": 0, "thinking": {"type": mode}},
                    request_id="original-fixture-request")
                current = self.config("normal", parameters={
                    "temperature": 1, "thinking": {
                        "type": "disabled" if mode == "enabled" else "enabled"},
                    "reasoning_effort": "none" if mode == "enabled" else "high"})
                frozen = server.prepare_frozen_ai_request(
                    "continue", "text", 3000, current, prepared["payload"],
                    request_id="retry-fixture-request")
                self.assertEqual(frozen["body"], prepared["body"])
                self.assertEqual(frozen["payload"], prepared["payload"])
                self.assertEqual(frozen["parameters"]["thinking"], {"type": mode})
                self.assertEqual(frozen["request_audit"]["request_sha256"],
                                 prepared["request_audit"]["request_sha256"])

    def test_multiline_heartbeats_and_reported_usage(self) -> None:
        delivered = []
        result = self.call("normal", stream=True, on_chunk=delivered.append)
        self.assertEqual(result["text"], "第一二段")
        self.assertEqual([item["seq"] for item in delivered], [1, 2])
        self.assertTrue(result["safe_to_apply"])
        self.assertEqual(result["usage"]["total_tokens"], 14)
        self.assertEqual(result["usage"]["prompt_tokens_details"]["cached_tokens"], 4)
        self.assertEqual(result["requested_model"], "requested-fixture")
        self.assertEqual(result["returned_model"], "actual-fixture")
        self.assertEqual(result["billing_state"], "usage_reported")
        self.assertEqual(result["request_audit"]["provider_request_id"], "provider-request-id")

    def test_empty_and_partial_usage_keep_known_fields_with_unknown_billing(self) -> None:
        for name, state, lower in (
                ("empty", "unknown", 0), ("prompt-only", "partial", 7),
                ("completion-only", "partial", 9), ("details-only", "unknown", 0)):
            with self.subTest(name=name):
                result = self.call("usage-" + name)
                self.assertTrue(result["safe_to_apply"])
                self.assertEqual(result["usage"], USAGE_CASES[name])
                self.assertEqual(result["usage_state"], state)
                self.assertEqual(result["billing_state"], "unknown")
                self.assertIsNone(result["usage_accounting"]["total_tokens"])
                self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], lower)

    def test_complete_usage_requires_valid_total_or_complete_components(self) -> None:
        for name, total, source in (
                ("components", 16, "derived_from_components"),
                ("total-only", 16, "provider_reported"), ("total-zero", 0, "provider_reported")):
            with self.subTest(name=name):
                result = self.call("usage-" + name)
                self.assertEqual(result["usage"], USAGE_CASES[name])
                self.assertEqual(result["usage_state"], "reported")
                self.assertEqual(result["billing_state"], "usage_reported")
                self.assertEqual(result["usage_accounting"]["total_tokens"], total)
                self.assertEqual(result["usage_accounting"]["source"], source)
                self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], total)

    def test_conflicting_usage_preserves_raw_counters_without_claiming_complete_bill(self) -> None:
        for name, lower in (("conflict", 16), ("component-exceeds-total", 17)):
            with self.subTest(name=name):
                result = self.call("usage-" + name)
                self.assertEqual(result["usage"], USAGE_CASES[name])
                self.assertEqual(result["usage_state"], "inconsistent")
                self.assertEqual(result["billing_state"], "unknown")
                self.assertIsNone(result["usage_accounting"]["total_tokens"])
                self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], lower)

    def test_invalid_token_types_never_become_reported_usage_or_lose_valid_counts(self) -> None:
        for name in ("bool", "negative", "float"):
            with self.subTest(name=name), self.assertRaises(ProviderError) as caught:
                self.call("usage-" + name)
            result = caught.exception.partial_result
            self.assertEqual(caught.exception.code, "invalid_response")
            self.assertEqual(result["usage"], USAGE_CASES[name])
            self.assertEqual(result["usage_state"], "inconsistent")
            self.assertEqual(result["billing_state"], "unknown")
            self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], 9)

    def test_stream_usage_merges_partial_fields_without_summing_repeated_snapshots(self) -> None:
        for mode, source in (("merge", "derived_from_components"), ("repeat", "provider_reported")):
            with self.subTest(mode=mode):
                result = self.call("usage-stream-" + mode, stream=True)
                self.assertEqual(result["usage"]["prompt_tokens"], 7)
                self.assertEqual(result["usage"]["completion_tokens"], 9)
                self.assertEqual(result["usage_state"], "reported")
                self.assertEqual(result["billing_state"], "usage_reported")
                self.assertEqual(result["usage_accounting"]["total_tokens"], 16)
                self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], 16)
                self.assertEqual(result["usage_accounting"]["source"], source)

    def test_stream_usage_regression_or_conflict_remains_untrusted_after_coherent_tail(self) -> None:
        for mode in ("regress", "conflict-then-correct"):
            with self.subTest(mode=mode):
                result = self.call("usage-stream-" + mode, stream=True)
                self.assertTrue(result["safe_to_apply"])
                self.assertEqual(result["usage_state"], "inconsistent")
                self.assertEqual(result["billing_state"], "unknown")
                self.assertIsNone(result["usage_accounting"]["total_tokens"])
                self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], 16)
                rechecked = usage_accounting(result["usage"], result["usage_accounting"])
                self.assertEqual(rechecked, result["usage_accounting"])

    def test_partial_usage_survives_stream_error_length_eof_and_cancel(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.call("usage-stream-partial-error", stream=True)
        error_result = caught.exception.partial_result
        self.assertEqual(error_result["text"], "保留正文")
        self.assertEqual(error_result["usage"], {"completion_tokens": 11})
        self.assertEqual(error_result["usage_state"], "partial")
        self.assertEqual(error_result["billing_state"], "unknown")
        self.assertEqual(error_result["usage_accounting"]["reported_tokens_lower_bound"], 11)
        for mode in ("partial-length", "partial-eof"):
            with self.subTest(mode=mode):
                result = self.call("usage-stream-" + mode, stream=True)
                self.assertEqual(result["usage"], {"completion_tokens": 9})
                self.assertEqual(result["billing_state"], "unknown")
                self.assertFalse(result["safe_to_apply"])
        cancel = threading.Event()
        def on_chunk(chunk):
            if "触发取消" in chunk["text"]:
                cancel.set()
        with self.assertRaises(ProviderCanceled) as caught:
            self.call("usage-stream-partial-cancel", stream=True,
                      on_chunk=on_chunk, cancel_event=cancel)
        self.assertEqual(caught.exception.partial_result["usage"], {"completion_tokens": 9})
        self.assertEqual(caught.exception.partial_result["usage_state"], "partial")
        self.assertEqual(caught.exception.billing_state, "unknown")

    def test_known_usage_survives_eof_and_partial_usage_survives_keyboard_interrupt(self) -> None:
        result = self.call("usage-stream-full-eof", stream=True)
        self.assertFalse(result["safe_to_apply"])
        self.assertEqual(result["billing_state"], "usage_reported")
        self.assertEqual(result["usage_accounting"]["total_tokens"], 16)
        def interrupted(_response, accumulator, _account, _assert_running):
            accumulator.payload({"choices": [], "usage": {"completion_tokens": 9}}, sse=True)
            raise KeyboardInterrupt
        with patch("moyu_provider._read_sse", side_effect=interrupted), \
                self.assertRaises(KeyboardInterrupt) as caught:
            self.call("normal", stream=True)
        result = caught.exception.partial_result
        self.assertEqual(result["usage"], {"completion_tokens": 9})
        self.assertEqual(result["usage_state"], "partial")
        self.assertEqual(result["billing_state"], "unknown")
        self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], 9)

    def test_usage_accounting_validates_saved_metadata_and_never_upgrades_unknown_evidence(self) -> None:
        usage = {"total_tokens": 5}
        for observation in (
                [], {"state": []}, {"state": "made-up"},
                {"state": "reported", "total_tokens": True,
                 "reported_tokens_lower_bound": 5, "source": "provider_reported"},
                {"state": "reported", "total_tokens": 5,
                 "reported_tokens_lower_bound": -1, "source": "provider_reported"},
                {"state": "reported", "total_tokens": 5,
                 "reported_tokens_lower_bound": 5, "source": "made-up"},
                {"state": "reported", "total_tokens": 5,
                 "reported_tokens_lower_bound": 5, "source": []},
                {"state": "unknown", "total_tokens": None,
                 "reported_tokens_lower_bound": 5, "source": "unknown"}):
            with self.subTest(observation=observation):
                result = usage_accounting(usage, observation)
                self.assertEqual(result["state"], "inconsistent")
                self.assertIsNone(result["total_tokens"])
                self.assertEqual(result["reported_tokens_lower_bound"], 5)
        observation = {"state": "partial", "total_tokens": None,
                       "reported_tokens_lower_bound": 5, "source": "partial_provider_counters"}
        self.assertEqual(usage_accounting(usage, observation)["state"], "partial")
        self.assertEqual(usage_accounting({"total_tokens": True})["state"], "inconsistent")
        self.assertEqual(usage_accounting({"completion_tokens": 1.5})["reported_tokens_lower_bound"], 0)

    def test_empty_key_does_not_change_any_text(self) -> None:
        result = self.call("normal", stream=True)
        self.assertEqual(result["text"], "".join(item["text"] for item in result["_chunks"]))
        self.assertNotIn("密钥", result["text"])
        self.assertIsNone(Handler.requests[-1]["authorization"])

    def test_chunks_arrive_before_model_finishes(self) -> None:
        seen = threading.Event()
        times = []
        started = time.monotonic()
        def receive(chunk: dict) -> None:
            times.append(time.monotonic() - started)
            seen.set()
        result = self.call("slow", stream=True, on_chunk=receive)
        self.assertTrue(seen.is_set())
        self.assertLess(times[0], 0.5)
        self.assertGreater(result["latency_ms"], 800)
        self.assertEqual(result["text"], "第一段第二段")

    def test_length_with_done_is_not_complete(self) -> None:
        result = self.call("length", stream=True)
        self.assertTrue(result["provider_sse_complete"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["completion_state"], "truncated")
        self.assertFalse(result["safe_to_apply"])

    def test_eof_and_missing_finish_are_unknown(self) -> None:
        for case in ("eof", "done-only", "unfinished-event"):
            with self.subTest(case=case):
                result = self.call(case, stream=True)
                self.assertEqual(result["completion_state"], "unknown")
                self.assertFalse(result["safe_to_apply"])
        self.assertEqual(self.call("unfinished-event", stream=True)["text"], "完整第一片")

    def test_empty_stream_markers_wait_for_valid_finish_and_done(self) -> None:
        result = self.call("empty-first-finish", stream=True)
        self.assertEqual(result["text"], "首段空标记后的正文")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertTrue(result["safe_to_apply"])
        self.assertEqual(result["protocol_observation"]["events"][0]["choices"][0]["finish_reason"], "")
        for case in ("empty-only-finish", "empty-finish-eof"):
            with self.subTest(case=case):
                result = self.call(case, stream=True)
                self.assertEqual(result["finish_reason"], "unknown")
                self.assertFalse(result["safe_to_apply"])
        with self.assertRaises(ProviderError):
            self.call("json-empty-finish")

    def test_filter_and_refusal_are_not_candidates(self) -> None:
        for case, expected in (("filter", "filtered"), ("refusal", "refused")):
            result = self.call(case, stream=True)
            self.assertEqual(result["completion_state"], expected)
            self.assertFalse(result["safe_to_apply"])

    def test_wrong_structured_types_fail_and_keep_candidate(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.call("wrong-schema", task="scene", response_format="json")
        self.assertEqual(caught.exception.code, "invalid_structured_output")
        self.assertIn('"pov"', caught.exception.partial_result["text"])
        self.assertFalse(caught.exception.partial_result["safe_to_apply"])

    def test_invalid_protocol_values_are_rejected(self) -> None:
        for case in ("wrong-usage", "wrong-content", "nan", "bad-event", "bad-utf8",
                     "model-change", "stream-error"):
            with self.subTest(case=case), self.assertRaises(ProviderError):
                self.call(case, stream=True)

    def test_response_size_is_bounded(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.call("size", config=self.config("size", max_response_bytes=1000))
        self.assertEqual(caught.exception.code, "response_too_large")
        self.assertEqual(caught.exception.billing_state, "unknown")

    def test_idle_and_total_deadline_stop_transport(self) -> None:
        for case, settings in (("idle", {"idle_timeout": 0.2}),
                               ("total", {"total_timeout": 0.25, "idle_timeout": 1})):
            before = len(Handler.requests)
            started = time.monotonic()
            with self.subTest(case=case), self.assertRaises(ProviderError) as caught:
                self.call(case, stream=True, config=self.config(case, **settings))
            self.assertIn(caught.exception.code, {"timeout", "connection_interrupted"})
            self.assertEqual(caught.exception.status, "unknown")
            self.assertLess(time.monotonic() - started, 0.8)
            self.assertEqual(len(Handler.requests), before + 1)

    def test_long_header_wait_and_json_thinking_use_total_deadline_not_stream_idle(self) -> None:
        for case in ("delayed-headers", "delayed-json"):
            with self.subTest(case=case):
                result = self.call(case, config=self.config(case, idle_timeout=0.1, total_timeout=1))
                self.assertTrue(result["safe_to_apply"])
                self.assertGreaterEqual(result["latency_ms"], 300)
                self.assertTrue(result["request_audit"]["dispatched"])

    def test_cancel_closes_reading_connection_and_marks_unknown_billing(self) -> None:
        cancel = threading.Event()
        def receive(_chunk: dict) -> None:
            cancel.set()
        started = time.monotonic()
        with self.assertRaises(ProviderCanceled) as caught:
            self.call("cancel", stream=True, cancel_event=cancel, on_chunk=receive)
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertEqual(caught.exception.status, "canceled")
        self.assertEqual(caught.exception.billing_state, "unknown")
        self.assertEqual(caught.exception.partial_result["text"], "第一段")
        self.assertFalse(caught.exception.partial_result["safe_to_apply"])

    def test_cancel_before_dispatch_has_no_call(self) -> None:
        cancel = threading.Event()
        cancel.set()
        before = len(Handler.requests)
        with self.assertRaises(ProviderCanceled) as caught:
            self.call("cancel", cancel_event=cancel)
        self.assertEqual(len(Handler.requests), before)
        self.assertEqual(caught.exception.billing_state, "not_dispatched")

    def test_dispatch_audit_runs_before_http_request(self) -> None:
        before = len(Handler.requests)
        audits = []
        def retain(audit: dict) -> None:
            self.assertEqual(len(Handler.requests), before)
            self.assertTrue(audit["dispatched"])
            self.assertEqual(audit["dispatch_state"], "dispatch_intent_recorded")
            audits.append(audit)
        result = self.call("json", on_dispatch=retain)
        self.assertEqual(len(audits), 1)
        self.assertEqual(len(Handler.requests), before + 1)
        self.assertEqual(result["request_audit"]["request_id"], audits[0]["request_id"])

    def test_failed_dispatch_persistence_prevents_http_request(self) -> None:
        before = len(Handler.requests)
        def fail(_audit: dict) -> None:
            raise OSError("private storage detail")
        with self.assertRaises(ProviderError) as caught:
            self.call("json", on_dispatch=fail)
        self.assertEqual(caught.exception.code, "dispatch_audit_failed")
        self.assertEqual(caught.exception.billing_state, "not_dispatched")
        self.assertFalse(caught.exception.partial_result["request_audit"]["dispatched"])
        self.assertEqual(len(Handler.requests), before)
        self.assertNotIn("private storage detail", str(caught.exception))

    def test_cancel_after_dispatch_intent_has_consistent_not_sent_result(self) -> None:
        before = len(Handler.requests)
        cancel = threading.Event()
        intents = []
        def cancel_after_intent(audit: dict) -> None:
            intents.append(dict(audit))
            cancel.set()
        with self.assertRaises(ProviderCanceled) as caught:
            self.call("json", cancel_event=cancel, on_dispatch=cancel_after_intent)
        self.assertTrue(intents[0]["dispatched"])
        self.assertFalse(caught.exception.request_dispatched)
        self.assertEqual(caught.exception.billing_state, "not_dispatched")
        audit = caught.exception.partial_result["request_audit"]
        self.assertFalse(audit["dispatched"])
        self.assertEqual(audit["dispatch_state"], "not_dispatched_after_intent")
        self.assertEqual(len(Handler.requests), before)

    def test_timeout_cannot_automatically_retry(self) -> None:
        before = len(Handler.requests)
        with self.assertRaises(ProviderError):
            self.call("idle", stream=True, config=self.config("idle", idle_timeout=0.15))
        self.assertEqual(len(Handler.requests), before + 1)

    def test_redirect_is_not_followed(self) -> None:
        before = len(Handler.requests)
        with self.assertRaises(ProviderError) as caught:
            self.call("redirect", config=self.config("redirect", api_key="fixture-sensitive-value"))
        self.assertEqual(caught.exception.http_status, 302)
        self.assertEqual(len(Handler.requests), before + 1)

    def test_secret_redaction_spans_network_chunks(self) -> None:
        for case in ("secret-json", "secret-stream", "usage-extra"):
            pieces = []
            result = self.call(case, stream=case == "secret-stream",
                               config=self.config(case, api_key="fixture-sensitive-value"),
                               on_chunk=pieces.append)
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("fixture-sensitive-value", serialized)
            self.assertEqual(result["text"], "".join(chunk["text"] for chunk in pieces))
        result = self.call("secret-stream", config=self.config("secret-stream", api_key="fixture-sensitive-value"), stream=True)
        self.assertEqual(result["text"], "前[密钥已隐藏]后")

    def test_failure_does_not_expose_provider_error_body_or_secret(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.call("http-error", config=self.config("http-error", api_key="fixture-sensitive-value"))
        self.assertNotIn("fixture-sensitive-value", str(caught.exception))
        self.assertNotIn("fixture-sensitive-value", json.dumps(caught.exception.partial_result))

    def test_secret_in_usage_field_names_is_redacted_recursively(self) -> None:
        result = self.call("usage-secret-key", config=self.config(
            "usage-secret-key", api_key="fixture-sensitive-value"))
        self.assertTrue(result["safe_to_apply"])
        self.assertEqual(result["usage"]["total_tokens"], 5)
        self.assertNotIn("fixture-sensitive-value", json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["usage"]["[密钥已隐藏]"]["nested-[密钥已隐藏]"], "metadata")
        self.assertEqual(result["usage"]["completion_tokens_details"]["detail-[密钥已隐藏]"], 1)

    def test_failed_chunk_persistence_stops_once(self) -> None:
        def fail(_chunk: dict) -> None:
            raise RuntimeError("private storage exception")
        with self.assertRaises(ProviderError) as caught:
            self.call("normal", stream=True, on_chunk=fail)
        self.assertEqual(caught.exception.code, "chunk_persistence_failed")
        self.assertNotIn("private storage exception", str(caught.exception))

    def test_nonstream_finish_reason_and_stream_json_fallback(self) -> None:
        result = self.call("json", stream=True)
        self.assertEqual(result["stream_transport"], "provider_json_fallback")
        self.assertTrue(result["safe_to_apply"])
        self.assertFalse(self.call("json-length")["safe_to_apply"])
        self.assertEqual(self.call("json-unknown")["completion_state"], "unknown")

    def test_unknown_finish_keeps_redacted_raw_reason_and_field_shape(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.call("vendor-finish", config=self.config("vendor-finish",
                                                        api_key="fixture-sensitive-value"))
        self.assertEqual(caught.exception.code, "invalid_response")
        result = caught.exception.partial_result
        shape = result["protocol_observation"]["events"][0]
        self.assertEqual(shape["choices"][0]["finish_reason"], "vendor-[密钥已隐藏]")
        self.assertEqual(shape["choices"][0]["message"]["fields"]["content"],
                         {"type": "string", "chars": 4})
        self.assertEqual(result["completion_state"], "unknown")
        self.assertNotIn("fixture-sensitive-value", json.dumps(result, ensure_ascii=False))

    def test_reasoning_is_observed_without_becoming_story_or_saved_content(self) -> None:
        result = self.call("reasoning-body", stream=True, config=self.config(
            "reasoning-body", api_key="fixture-sensitive-value"))
        self.assertEqual(result["text"], "只有真实正文")
        events = result["protocol_observation"]["events"]
        self.assertEqual(events[0]["choices"][0]["delta"]["fields"]["reasoning_content"],
                         {"type": "string", "chars": len("私密思考全文不可保存")})
        observation = json.dumps(result["protocol_observation"], ensure_ascii=False)
        for text in ("私密思考全文不可保存", "只有真实正文", "不可保存的字段值", "fixture-sensitive-value"):
            self.assertNotIn(text, observation)
        self.assertIn("[密钥已隐藏]", observation)
        self.assertEqual(result["reasoning_observation"], {
            "field_events": 2, "nonempty_events": 2, "invalid_events": 0,
            "characters": 2 * len("私密思考全文不可保存"),
            "requested_disabled": False, "unexpected_reasoning": False,
        })

    def test_disabled_thinking_with_nonempty_reasoning_is_observed_without_rewriting_protocol(self) -> None:
        for case, stream, parameters in (
                ("reasoning-json", False, {"thinking": {"type": "disabled"}}),
                ("reasoning-body", True, {"thinking": {"type": "disabled"}}),
                ("reasoning-json", False, {"reasoning_effort": "none"}),
                ("reasoning-middle", True, {"thinking": {"type": "disabled"}})):
            with self.subTest(case=case, parameters=parameters):
                before = len(Handler.requests)
                delivered = []
                result = self.call(case, stream=stream, parameters=parameters,
                                   on_chunk=delivered.append)
                self.assertEqual(len(Handler.requests), before + 1)
                self.assertEqual(result["text"], "铁" if case == "reasoning-json" else "只有真实正文")
                self.assertEqual(result["text"], "".join(chunk["text"] for chunk in delivered))
                self.assertEqual(result["completion_state"], "complete")
                self.assertEqual(result["finish_reason"], "stop")
                self.assertFalse(result["truncated"])
                self.assertTrue(result["safe_to_apply"])
                self.assertEqual(result["usage_state"], "reported")
                self.assertEqual(result["billing_state"], "usage_reported")
                self.assertEqual(result["usage"]["total_tokens"], 16)
                observation = result["reasoning_observation"]
                self.assertTrue(observation["requested_disabled"])
                self.assertTrue(observation["unexpected_reasoning"])
                expected_events = 2 if case == "reasoning-body" else 1
                self.assertEqual(observation["nonempty_events"], expected_events)
                self.assertEqual(observation["characters"], expected_events * len("私密思考全文不可保存"))
                self.assertNotIn("私密思考全文不可保存", json.dumps(result, ensure_ascii=False))
                if case == "reasoning-middle":
                    self.assertTrue(result["protocol_observation"]["events_truncated"])
                    self.assertTrue(all(
                        item.get("choices", [{}])[0].get("delta", {}).get("fields", {}).get(
                            "reasoning_content", {}).get("chars", 0) == 0
                        for item in result["protocol_observation"]["events"]))

    def test_enabled_thinking_and_empty_reasoning_field_do_not_trigger_mismatch(self) -> None:
        for case, parameters in (
                ("reasoning-json", {"thinking": {"type": "enabled"}}),
                ("reasoning-body", {"thinking": {"type": "enabled"}}),
                ("reasoning-json-empty", {"thinking": {"type": "disabled"}})):
            with self.subTest(case=case):
                result = self.call(case, stream=case == "reasoning-body", parameters=parameters)
                self.assertTrue(result["safe_to_apply"])
                self.assertFalse(result["reasoning_observation"]["unexpected_reasoning"])
                self.assertNotIn("私密思考全文不可保存", json.dumps(result, ensure_ascii=False))

    def test_reasoning_alias_and_invalid_shapes_are_counted_without_retaining_contents(self) -> None:
        for transport in ("json", "stream"):
            for name, fields in REASONING_FIELDS.items():
                with self.subTest(transport=transport, shape=name):
                    result = self.call("reasoning-variant-" + transport + "-" + name,
                                       stream=transport == "stream",
                                       parameters={"thinking": {"type": "disabled"}})
                    multiplier = 2 if transport == "stream" else 1
                    nonempty = sum(isinstance(value, str) and bool(value.strip())
                                   for value in fields.values()) * multiplier
                    invalid = sum(value is not None and not isinstance(value, str)
                                  for value in fields.values()) * multiplier
                    self.assertEqual(result["reasoning_observation"], {
                        "field_events": len(fields) * multiplier,
                        "nonempty_events": nonempty, "invalid_events": invalid,
                        "characters": sum(len(value) for value in fields.values()
                                          if isinstance(value, str)) * multiplier,
                        "requested_disabled": True,
                        "unexpected_reasoning": nonempty > 0 or invalid > 0,
                    })
                    self.assertEqual(reasoning_field_counts(result), (nonempty, invalid))
                    self.assertEqual(result["text"], "只有真实正文")
                    self.assertEqual(result["completion_state"], "complete")
                    self.assertEqual(result["finish_reason"], "stop")
                    self.assertTrue(result["safe_to_apply"])
                    self.assertEqual(result["usage"]["total_tokens"], 16)
                    for private in ("私密思考全文不可保存", "第二个私密字段"):
                        self.assertNotIn(private, json.dumps(result, ensure_ascii=False))
        allowed = self.call("reasoning-variant-json-alias",
                            parameters={"thinking": {"type": "enabled"}})
        self.assertFalse(allowed["reasoning_observation"]["unexpected_reasoning"])
        self.assertTrue(allowed["safe_to_apply"])

    def test_invalid_reasoning_count_survives_bounded_shape_truncation(self) -> None:
        result = self.call("reasoning-variant-stream-invalid-middle", stream=True,
                           parameters={"reasoning_effort": "none"})
        self.assertEqual(reasoning_field_counts(result), (0, 1))
        self.assertTrue(result["reasoning_observation"]["unexpected_reasoning"])
        self.assertTrue(result["protocol_observation"]["events_truncated"])
        self.assertTrue(all(item.get("choices", [{}])[0].get("delta", {}).get(
            "fields", {}).get("reasoning", {}).get("type") != "object"
            for item in result["protocol_observation"]["events"]))
        self.assertTrue(result["safe_to_apply"])
        self.assertEqual(result["text"], "只有真实正文")

    def test_reasoning_only_empty_reply_retains_protocol_evidence(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.call("reasoning-only", stream=True)
        self.assertEqual(caught.exception.code, "empty_response")
        result = caught.exception.partial_result
        self.assertEqual(result["text"], "")
        self.assertFalse(result["safe_to_apply"])
        self.assertEqual(result["protocol_observation"]["events_observed"], 3)
        self.assertEqual(result["protocol_observation"]["events"][-1]["choices"][0]["finish_reason"], "stop")
        self.assertTrue(result["provider_sse_complete"])

    def test_bounded_protocol_observation_retains_failure_at_stream_tail(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.call("tail-failure", stream=True)
        observation = caught.exception.partial_result["protocol_observation"]
        self.assertEqual(observation["events_observed"], 131)
        self.assertEqual(observation["events_recorded"], 128)
        self.assertTrue(observation["events_truncated"])
        self.assertEqual([item["event_sequence"] for item in observation["events"][:112]],
                         list(range(1, 113)))
        self.assertEqual(observation["events"][-1]["event_sequence"], 131)
        self.assertEqual(observation["events"][-1]["choices"][0]["finish_reason"], "vendor-failure-at-tail")

    def test_interrupt_after_usage_keeps_partial_story_usage_and_protocol(self) -> None:
        def interrupted(_response, accumulator, _account, _assert_running):
            accumulator.payload({"choices": [{"delta": {"content": "已到达的正文"},
                                                "finish_reason": None}]}, sse=True)
            accumulator.payload({"choices": [], "usage": {
                "prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13}}, sse=True)
            raise KeyboardInterrupt
        with patch("moyu_provider._read_sse", side_effect=interrupted), \
                self.assertRaises(KeyboardInterrupt) as caught:
            self.call("normal", stream=True)
        result = caught.exception.partial_result
        self.assertEqual(result["text"], "已到达的正文")
        self.assertEqual(result["usage"]["total_tokens"], 13)
        self.assertEqual(result["billing_state"], "usage_reported")
        self.assertEqual(result["completion_state"], "unknown")
        self.assertFalse(result["safe_to_apply"])
        self.assertEqual(result["protocol_observation"]["events_observed"], 2)
        self.assertTrue(result["request_audit"]["dispatched"])

    def test_zero_temperature_purpose_model_and_exact_request_audit(self) -> None:
        config = self.config("json", temperature=0, task_models={"continue": "purpose-fixture"})
        result = self.call("json", config=config, max_tokens=123)
        sent = Handler.requests[-1]["payload"]
        self.assertEqual(sent["temperature"], 0)
        self.assertEqual(sent["model"], "purpose-fixture")
        self.assertEqual(result["request_audit"]["payload"], sent)
        self.assertEqual(sent["max_tokens"], 123)

    def test_request_validation_prevents_malformed_calls(self) -> None:
        invalid = [
            {"parameters": {"temperature": False}},
            {"parameters": {"temperature": float("nan")}},
            {"parameters": {"dangerous": "unused"}},
            {"max_tokens": True},
            {"config": self.config("json", base_url="http://example.com/v1")},
            {"config": self.config("json", api_key="bad\nheader")},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ProviderError):
                self.call("json", **kwargs)

    def test_schema_accepts_valid_json_task(self) -> None:
        from moyu_provider import validate_json, TASK_SCHEMAS
        validate_json({"pov": "林岚", "location": "码头", "goal": "找人",
                       "obstacle": "封锁", "exit": "开门"}, TASK_SCHEMAS["scene"])
        with self.assertRaises(ProviderError):
            validate_json({"directions": [{"name": "转向", "cost": 2, "surprise": "相遇"}]},
                          TASK_SCHEMAS["brainstorm"])
        prepared = prepare_chat_request(self.config("json"), [{"role": "user", "content": "test"}],
                                        parameters={"temperature": 0})
        self.assertEqual(prepared["payload"]["temperature"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
