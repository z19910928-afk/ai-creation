#!/usr/bin/env python3
"""Native Responses protocol checks against local, synthetic HTTP fixtures."""
from __future__ import annotations

import copy
import hashlib
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
    ProviderCanceled, ProviderError, call_chat_completion, list_models,
    normalize_json_completion, parse_sse_completion, prepare_chat_request,
    request_messages, usage_accounting,
)


SECRET = "synthetic-responses-credential"
USAGE = {
    "input_tokens": 7, "input_tokens_details": {"cached_tokens": 2},
    "output_tokens": 9, "output_tokens_details": {"reasoning_tokens": 3},
    "total_tokens": 16,
}
MESSAGES = [
    {"role": "system", "content": "只返回正文或规定的 JSON"},
    {"role": "developer", "content": "保持角色和顺序"},
    {"role": "user", "content": "写一段"},
    {"role": "assistant", "content": "上一段"},
    {"role": "user", "content": "接着写"},
]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def text_part(text="安全正文"):
    return {"type": "output_text", "text": text, "annotations": []}


def item(text="安全正文", *, identifier="msg-local", status="completed", content=None):
    return {"type": "message", "id": identifier, "role": "assistant", "status": status,
            "content": [text_part(text)] if content is None else content}


def response(text="安全正文", *, status="completed", output=None, **extra):
    return {
        "id": "resp-local", "object": "response", "status": status,
        "model": "actual-local", "usage": copy.deepcopy(USAGE),
        "output": [item(text, status="completed" if status == "completed" else "incomplete")]
        if output is None else output,
        "error": None, "incomplete_details": None, **extra,
    }


def delta(text, *, index=0, content_index=0, item_id="msg-local"):
    return {"type": "response.output_text.delta", "output_index": index,
            "content_index": content_index, "item_id": item_id, "delta": text}


def terminal(value=None, *, kind="response.completed"):
    return {"type": kind, "response": response() if value is None else value}


def stream_bytes(events, *, done=False):
    chunks = [b": heartbeat\r\n\r\n"]
    for index, original in enumerate(events):
        value = {"sequence_number": index, **original}
        chunks.append(b"event: " + value["type"].encode() + b"\ndata: "
                      + encoded(value) + b"\n\n")
    if done:
        chunks.append(b"data: [DONE]\n\n")
    return b"".join(chunks)


def native_events(text="第一段第二段"):
    return [
        {"type": "response.created", "response": response(status="in_progress", output=[])},
        {"type": "response.output_item.added", "output_index": 0,
         "item": item(status="in_progress", content=[])},
        {"type": "response.content_part.added", "output_index": 0, "content_index": 0,
         "item_id": "msg-local", "part": text_part("")},
        delta(text[:3]), delta(text[3:]),
        {"type": "response.output_text.done", "output_index": 0, "content_index": 0,
         "item_id": "msg-local", "text": text},
        {"type": "response.content_part.done", "output_index": 0, "content_index": 0,
         "item_id": "msg-local", "part": text_part(text)},
        {"type": "response.output_item.done", "output_index": 0, "item": item(text)},
        terminal(response(text)),
    ]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []
    scripts = {}
    lock = threading.Lock()

    def _record(self, payload=None):
        with self.lock:
            self.requests.append({"method": self.command, "path": self.path,
                                  "payload": payload,
                                  "authorization": self.headers.get("Authorization")})

    def do_GET(self):
        self._record()
        body = encoded({"data": [{"id": "local-model"}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self._record(payload)
        case = self.path.split("/")[1]
        status, content_type, pieces = self.scripts[case]
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Request-Id", "local-provider-id")
        self.send_header("Location", "/must-not-follow/responses")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for value in pieces:
                if isinstance(value, (float, int)):
                    time.sleep(value)
                else:
                    self.wfile.write(value)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


class ResponsesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def config(self, case="json", **extra):
        return {"base_url": self.base + "/" + case + "/v1", "model": "requested-local",
                "api_key": "", "api_protocol": "responses", "total_timeout": 4, **extra}

    def call(self, case="json", *, value=None, events=None, pieces=None,
             status=200, content_type=None, config=None, **kwargs):
        if pieces is None:
            pieces = [encoded(response() if value is None else value)] if events is None else [
                stream_bytes(events)]
        Handler.scripts[case] = (
            status, content_type or ("application/json" if events is None else "text/event-stream"),
            pieces,
        )
        return call_chat_completion(config or self.config(case), MESSAGES, **kwargs)

    def parse(self, events, **kwargs):
        return parse_sse_completion(io.BytesIO(stream_bytes(events)),
                                    api_protocol="responses", **kwargs)

    def normalize(self, value=None, **kwargs):
        return normalize_json_completion(response() if value is None else value,
                                         api_protocol="responses", **kwargs)

    def test_request_exact_native_fields_roles_hash_and_no_storage(self):
        config = self.config(include_usage=True, output_token_parameter="max_completion_tokens")
        prepared = prepare_chat_request(config, MESSAGES, max_tokens=123, stream=True,
                                        response_format="json",
                                        parameters={"temperature": 0, "top_p": 0.9,
                                                    "reasoning_effort": "none"})
        expected = {
            "model": "requested-local", "input": MESSAGES, "max_output_tokens": 123,
            "temperature": 0, "top_p": 0.9, "reasoning": {"effort": "none"},
            "stream": True, "store": False, "text": {"format": {"type": "json_object"}},
        }
        self.assertEqual(prepared["payload"], expected)
        self.assertEqual(json.loads(prepared["body"]), expected)
        self.assertEqual(prepared["request_audit"]["payload"], expected)
        self.assertEqual(prepared["api_protocol"], "responses")
        self.assertEqual(prepared["request_audit"]["api_protocol"], "responses")
        self.assertEqual(prepared["request_audit"]["request_sha256"],
                         hashlib.sha256(prepared["body"]).hexdigest())
        self.assertEqual(prepared["request_audit"]["messages_sha256"],
                         hashlib.sha256(encoded(MESSAGES)).hexdigest())
        self.assertIs(request_messages(prepared["payload"], "responses"), MESSAGES)

    def test_both_copied_endpoints_are_normalized_for_explicit_protocol(self):
        for protocol, endpoint in (("responses", "responses"),
                                   ("chat_completions", "chat/completions")):
            for suffix in ("/v1", "/v1/", "/v1/chat/completions",
                           "/v1/responses", "/v1/responses/"):
                with self.subTest(protocol=protocol, suffix=suffix):
                    prepared = prepare_chat_request(
                        self.config(base_url=self.base + suffix, api_protocol=protocol), MESSAGES)
                    self.assertEqual(prepared["path"], "/v1/" + endpoint)
                    self.assertEqual(prepared["endpoint"], self.base + "/v1/" + endpoint)

    def test_absent_protocol_preserves_chat_payload(self):
        config = self.config()
        config.pop("api_protocol")
        prepared = prepare_chat_request(config, MESSAGES, max_tokens=1, stream=True)
        self.assertEqual(prepared["payload"], {
            "model": "requested-local", "messages": MESSAGES, "max_tokens": 1,
            "temperature": 0.65, "stream": True})
        self.assertEqual(prepared["api_protocol"], "chat_completions")

    def test_models_get_same_endpoint_and_validate_protocol_without_generation(self):
        before = len(Handler.requests)
        for protocol in ("chat_completions", "responses"):
            result = list_models(self.config(
                base_url=self.base + "/models/v1/responses",
                api_protocol=protocol, api_key=SECRET))
            self.assertEqual(result["models"], ["local-model"])
            self.assertEqual(result["endpoint"], self.base + "/models/v1/models")
        self.assertEqual(len(Handler.requests), before + 2)
        self.assertTrue(all(row["method"] == "GET" for row in Handler.requests[before:]))
        for protocol in (None, "", "Responses", False, {}, []):
            with self.subTest(protocol=protocol), self.assertRaises(ProviderError):
                list_models(self.config(api_protocol=protocol))
        self.assertEqual(len(Handler.requests), before + 2)

    def test_unsupported_chat_parameters_fail_before_dispatch_in_every_layer(self):
        before = len(Handler.requests)
        for name, value in (("stop", ["end"]), ("seed", 1), ("presence_penalty", 0),
                            ("frequency_penalty", 0), ("thinking", {"type": "disabled"})):
            for layer in ("parameters", "task_parameters", "override"):
                config = self.config()
                kwargs = {}
                if layer == "task_parameters":
                    config[layer] = {"continue": {name: value}}
                elif layer == "override":
                    kwargs["parameters"] = {name: value}
                else:
                    config[layer] = {name: value}
                with self.subTest(name=name, layer=layer), self.assertRaises(ProviderError) as caught:
                    self.call(config=config, **kwargs)
                self.assertEqual(caught.exception.code, "invalid_request")
                self.assertEqual(caught.exception.billing_state, "not_dispatched")
                self.assertIn(name, str(caught.exception))
        self.assertEqual(len(Handler.requests), before)

    def test_invalid_protocol_length_and_named_messages_are_rejected(self):
        for protocol in (None, False, [], {}, "chat", "Responses"):
            with self.subTest(protocol=protocol), self.assertRaises(ProviderError):
                prepare_chat_request(self.config(api_protocol=protocol), MESSAGES)
        for length in (True, 0, 1, 15, 128001):
            with self.subTest(length=length), self.assertRaises(ProviderError):
                prepare_chat_request(self.config(), MESSAGES, max_tokens=length)
        with self.assertRaises(ProviderError):
            prepare_chat_request(self.config(), [{"role": "user", "content": "x", "name": "author"}])
        for messages in ([{"role": [], "content": "x"}], [], "text"):
            with self.subTest(messages=messages), self.assertRaises(ProviderError):
                prepare_chat_request(self.config(), messages)

    def test_message_helper_rejects_ambiguity_and_wrong_protocol(self):
        for payload, protocol in (
                ({"input": MESSAGES}, "chat_completions"),
                ({"messages": MESSAGES}, "responses"),
                ({"input": MESSAGES, "messages": MESSAGES}, "responses"),
                ({"input": "flattened prompt"}, "responses"),
                ({"input": [{"role": "user", "content": []}]}, "responses"),
                ({"input": MESSAGES}, "unknown"),
                (None, "responses")):
            with self.subTest(payload=payload, protocol=protocol), self.assertRaises(ProviderError):
                request_messages(payload, protocol)
        self.assertIs(request_messages({"messages": MESSAGES}), MESSAGES)

    def test_json_success_retains_model_usage_and_native_counters(self):
        result = self.call()
        self.assertEqual(result["text"], "安全正文")
        self.assertEqual(result["api_protocol"], "responses")
        self.assertEqual(result["responses_status"], "completed")
        self.assertTrue(result["safe_to_apply"])
        self.assertEqual(result["returned_model"], "actual-local")
        self.assertEqual(result["requested_model"], "requested-local")
        self.assertEqual(result["usage"]["input_tokens"], 7)
        self.assertEqual(result["usage"]["prompt_tokens"], 7)
        self.assertEqual(result["usage"]["completion_tokens"], 9)
        self.assertEqual(result["usage"]["prompt_tokens_details"]["cached_tokens"], 2)
        self.assertEqual(result["usage"]["completion_tokens_details"]["reasoning_tokens"], 3)
        self.assertEqual(result["billing_state"], "usage_reported")
        self.assertEqual(usage_accounting(result["usage"], result["usage_accounting"]),
                         result["usage_accounting"])
        self.assertEqual(Handler.requests[-1]["path"], "/json/v1/responses")

    def test_nonstream_can_fall_back_from_stream_without_fake_native_stream(self):
        result = self.call(stream=True)
        self.assertTrue(result["safe_to_apply"])
        self.assertEqual(result["stream_transport"], "provider_json_fallback")
        self.assertIsNone(result["provider_sse_complete"])

    def test_native_stream_reconciles_all_snapshots_without_duplicate_text(self):
        chunks = []
        result = self.call(events=native_events(), stream=True, on_chunk=chunks.append)
        self.assertTrue(result["safe_to_apply"])
        self.assertTrue(result["provider_sse_complete"])
        self.assertEqual(result["text"], "第一段第二段")
        self.assertEqual(result["text"], "".join(row["text"] for row in chunks))
        self.assertEqual([row["seq"] for row in chunks], [1, 2])
        self.assertEqual(result["stream_transport"], "provider_native_sse")
        self.assertEqual(result["request_audit"]["api_protocol"], "responses")

    def test_done_or_terminal_can_supply_missing_suffix_once(self):
        for events in (
                [delta("安"), terminal()],
                [{"type": "response.output_text.done", "output_index": 0, "content_index": 0,
                  "item_id": "msg-local", "text": "安全正文"}, terminal()],
                [terminal()],
                [delta("安"), {"type": "response.output_text.done", "output_index": 0,
                               "content_index": 0, "item_id": "msg-local", "text": "安全正文"},
                 terminal()]):
            with self.subTest(events=events):
                result = self.parse(events)
                self.assertEqual(result["text"], "安全正文")
                self.assertTrue(result["safe_to_apply"])
                self.assertEqual(result["text"], "".join(row["text"] for row in result["_chunks"]))

    def test_multiple_content_parts_and_messages_keep_canonical_order(self):
        final = response(output=[
            item(content=[text_part("甲"), text_part("乙")]),
            item("丙", identifier="msg-second")])
        result = self.parse([
            delta("甲"), delta("乙", content_index=1),
            delta("丙", index=1, item_id="msg-second"), terminal(final)])
        self.assertEqual(result["text"], "甲乙丙")
        self.assertTrue(result["safe_to_apply"])

    def test_final_mismatch_does_not_turn_persisted_candidate_into_success(self):
        for final in (
                response("不同"), response(output=[]),
                response(output=[item("首", identifier="msg-first"), item("安全正文")]),
                response(output=[item("安全正文", identifier="changed-id")])):
            with self.subTest(final=final), self.assertRaises(ProviderError) as caught:
                self.call(events=[delta("安全正文"), terminal(final)], stream=True)
            self.assertFalse(caught.exception.partial_result["safe_to_apply"])
            self.assertEqual(caught.exception.partial_result["text"], "安全正文")
            self.assertEqual(caught.exception.partial_result["api_protocol"], "responses")

    def test_duplicate_sequence_and_out_of_order_parts_fail(self):
        for events in (
                [delta("甲"), {**delta("乙"), "sequence_number": 0}],
                [delta("乙", content_index=1), delta("甲")],
                [delta("甲"), {**delta("乙"), "item_id": "different"}],
                [{"type": "response.output_text.done", "output_index": 0, "content_index": 0,
                  "item_id": "msg-local", "text": "甲"}, delta("乙")]):
            with self.subTest(events=events), self.assertRaises(ProviderError):
                self.parse(events)

    def test_eof_without_native_terminal_stays_unknown(self):
        for pieces in (
                [stream_bytes([delta("半段")])],
                [stream_bytes([delta("半段")]), b"event: response.completed\ndata: "],
                [stream_bytes([delta("半段")]), b"data: " + encoded(terminal())]):
            result = self.call(pieces=pieces, content_type="text/event-stream", stream=True)
            self.assertEqual(result["text"], "半段")
            self.assertEqual(result["completion_state"], "unknown")
            self.assertFalse(result["safe_to_apply"])
            self.assertFalse(result["provider_sse_complete"])

    def test_done_marker_cannot_impersonate_responses_completion(self):
        with self.assertRaises(ProviderError) as caught:
            self.call(pieces=[stream_bytes([delta("半段")], done=True)],
                      content_type="text/event-stream", stream=True)
        self.assertEqual(caught.exception.code, "invalid_response")
        self.assertFalse(caught.exception.partial_result["safe_to_apply"])
        self.assertEqual(caught.exception.partial_result["text"], "半段")

    def test_incomplete_and_filter_are_never_success_in_json_or_sse(self):
        for reason, expected in (("max_output_tokens", "truncated"), ("content_filter", "filtered")):
            value = response("半段", status="incomplete", incomplete_details={"reason": reason})
            for native in (False, True):
                with self.subTest(reason=reason, native=native):
                    result = self.call(
                        value=value, events=[delta("半"), terminal(value, kind="response.incomplete")]
                        if native else None, stream=native)
                    self.assertEqual(result["text"], "半段")
                    self.assertEqual(result["completion_state"], expected)
                    self.assertFalse(result["safe_to_apply"])
                    self.assertTrue(result["truncated"])

    def test_refusal_and_tool_calls_never_become_applyable_story(self):
        refusal = response(output=[item(content=[{"type": "refusal", "refusal": "不能生成"}])])
        tool = response(output=[{"id": "fc-local", "type": "function_call", "status": "completed",
                                 "name": "write", "call_id": "call-local", "arguments": "{}"}])
        for value, expected in ((refusal, "refused"), (tool, "tool_call")):
            for native in (False, True):
                with self.subTest(expected=expected, native=native):
                    result = self.call(value=value, events=[terminal(value)] if native else None,
                                       stream=native)
                    self.assertEqual(result["completion_state"], expected)
                    self.assertFalse(result["safe_to_apply"])
        mixed = response(output=[
            {"id": "fc-local", "type": "function_call", "arguments": "{}"},
            item("正文", identifier="msg-mixed")])
        self.assertEqual(self.normalize(mixed)["completion_state"], "tool_call")

    def test_refusal_and_tool_events_remain_sticky_at_terminal(self):
        for event in (
                {"type": "response.refusal.delta", "output_index": 0, "content_index": 0,
                 "item_id": "msg-local", "delta": "拒绝"},
                {"type": "response.function_call_arguments.delta", "output_index": 1,
                 "item_id": "fc-local", "delta": "{}"}):
            final = response()
            if "function_call" in event["type"]:
                final["output"].append({"id": "fc-local", "type": "function_call",
                                        "arguments": "{}"})
            result = self.parse([event, terminal(final)])
            self.assertFalse(result["safe_to_apply"])
            self.assertIn(result["completion_state"], {"tool_call", "refused"})

    def test_provider_failure_and_error_keep_usage_partial_text_and_no_secret(self):
        for event in (
                terminal(response(status="failed", output=[],
                                  error={"message": SECRET}), kind="response.failed"),
                {"type": "error", "code": "server_error", "message": SECRET, "usage": USAGE}):
            with self.subTest(event=event), self.assertRaises(ProviderError) as caught:
                self.call(events=[delta("保留正文"), event], stream=True,
                          config=self.config(api_key=SECRET))
            failure = caught.exception
            self.assertEqual(failure.code, "provider_stream_error")
            self.assertEqual(failure.partial_result["text"], "保留正文")
            self.assertFalse(failure.partial_result["safe_to_apply"])
            self.assertEqual(failure.billing_state, "usage_reported")
            self.assertNotIn(SECRET, str(failure))
            self.assertNotIn(SECRET, json.dumps(failure.partial_result))

    def test_response_identity_and_model_cannot_change_between_events(self):
        for final in (response(id="changed"), response(model="changed"), response(model=False)):
            events = [
                {"type": "response.created", "response": response(status="in_progress", output=[])},
                delta("安全正文"), terminal(final)]
            with self.subTest(final=final), self.assertRaises(ProviderError):
                self.parse(events)

    def test_missing_model_is_unknown_and_is_not_inferred_from_response_id(self):
        value = response()
        value.pop("model")
        self.assertIsNone(self.normalize(value)["returned_model"])
        events = [delta("安全正文"), terminal(value)]
        self.assertIsNone(self.parse(events)["returned_model"])

    def test_invalid_native_shapes_and_statuses_fail_without_builtin_errors(self):
        values = [
            response(status=[]), response(status=True), response(status="invented"),
            response(output={}), response(output=[None]),
            response(output=[item(status="in_progress")]),
            response(output=[{**item(), "role": "user"}]),
            response(output=[{**item(), "status": []}]),
            response(output=[item(content=[{"type": "output_text", "text": False}])]),
            response(output=[item(content=[{"type": "image", "data": "fake"}])]),
            response(output=[item(content=[{"type": "refusal", "refusal": []}])]),
            response(output=[{**item(), "id": None}]),
            response(output=[{**item(), "type": {}}]),
            response(incomplete_details={"reason": "max_output_tokens"}),
            response(status="incomplete", incomplete_details={"reason": []}),
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ProviderError):
                self.normalize(value)

    def test_native_event_type_terminal_conflict_and_chat_payload_are_rejected(self):
        events = [
            {"type": "response.invented"},
            terminal(response(status="in_progress")),
            terminal(response(), kind="response.incomplete"),
            {"type": "response.completed"},
            {**delta("x"), "output_index": True},
            {**delta("x"), "content_index": -1},
            {**delta("x"), "delta": []},
            {**delta("x"), "sequence_number": True},
        ]
        for event in events:
            with self.subTest(event=event), self.assertRaises(ProviderError):
                self.parse([event])
        with self.assertRaises(ProviderError):
            parse_sse_completion(io.BytesIO(
                b'event: response.completed\ndata: {"type":"response.in_progress"}\n\n'),
                api_protocol="responses")
        with self.assertRaises(ProviderError):
            self.normalize({"choices": [{"message": {"content": "fake"}, "finish_reason": "stop"}]})

    def test_structured_output_remains_validated_against_task_schema(self):
        text = '{"content":"正文"}'
        for native in (False, True):
            result = self.call(value=response(text),
                               events=native_events(text) if native else None,
                               response_format="json", stream=native)
            self.assertEqual(result["structured"], {"content": "正文"})
        for text in ('{"content":[]}', '{"wrong":"字段"}', "not json"):
            with self.subTest(text=text), self.assertRaises(ProviderError) as caught:
                self.call(events=native_events(text), response_format="json", stream=True)
            self.assertEqual(caught.exception.code, "invalid_structured_output")
            self.assertEqual(caught.exception.partial_result["text"], text)
            self.assertFalse(caught.exception.partial_result["safe_to_apply"])

    def test_reasoning_is_not_story_or_audit_content_and_disabled_is_observed(self):
        hidden = "这段私密思考不应出现在结果中"
        final = response(output=[
            {"id": "rs-local", "type": "reasoning",
             "summary": [{"type": "summary_text", "text": hidden}],
             "encrypted_content": hidden},
            item("正文")])
        result = self.call(events=[
            {"type": "response.reasoning_summary_text.delta", "item_id": "rs-local",
             "output_index": 0, "summary_index": 0, "delta": hidden},
            delta("正文", index=1), terminal(final)], stream=True,
            parameters={"reasoning_effort": "none"})
        self.assertEqual(result["text"], "正文")
        self.assertTrue(result["reasoning_observation"]["requested_disabled"])
        self.assertTrue(result["reasoning_observation"]["unexpected_reasoning"])
        self.assertNotIn(hidden, json.dumps(result, ensure_ascii=False))

    def test_native_reasoning_content_parts_are_counted_without_becoming_story(self):
        hidden = "另一个不能进入正文的思考片段"
        reasoning = {"type": "reasoning_text", "text": hidden}
        final = response(output=[
            {"id": "rs-local", "type": "reasoning", "summary": [], "content": [reasoning]},
            item("正文")])
        result = self.parse([
            {"type": "response.content_part.added", "output_index": 0, "content_index": 0,
             "item_id": "rs-local", "part": {"type": "reasoning_text", "text": ""}},
            {"type": "response.reasoning_text.delta", "output_index": 0, "content_index": 0,
             "item_id": "rs-local", "delta": hidden},
            {"type": "response.content_part.done", "output_index": 0, "content_index": 0,
             "item_id": "rs-local", "part": reasoning},
            delta("正文", index=1), terminal(final)])
        self.assertTrue(result["safe_to_apply"])
        self.assertEqual(result["text"], "正文")
        self.assertGreater(result["reasoning_observation"]["nonempty_events"], 0)
        self.assertNotIn(hidden, json.dumps(result, ensure_ascii=False))

    def test_empty_completed_output_cannot_claim_success(self):
        for value in (response(output=[]), response(""), response(" \n"),
                      response(output=[{"id": "rs-local", "type": "reasoning", "summary": []}])):
            for native in (False, True):
                with self.subTest(value=value, native=native), self.assertRaises(ProviderError) as caught:
                    self.call(value=value, events=[terminal(value)] if native else None, stream=native)
                self.assertEqual(caught.exception.code, "empty_response")
                self.assertFalse(caught.exception.partial_result["safe_to_apply"])

    def test_secret_redaction_across_deltas_and_terminal_snapshot(self):
        text = "前" + SECRET + "后"
        chunks = []
        result = self.call(events=native_events(text), stream=True, on_chunk=chunks.append,
                           config=self.config(api_key=SECRET))
        self.assertEqual(result["text"], "前[密钥已隐藏]后")
        self.assertEqual(result["text"], "".join(chunk["text"] for chunk in chunks))
        self.assertNotIn(SECRET, json.dumps(result))
        result = self.call(value=response(text), config=self.config(api_key=SECRET))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_usage_partial_regression_invalid_types_and_missing_are_honest(self):
        cases = [
            (None, "unknown", None), ({}, "unknown", None),
            ({"input_tokens": 7}, "partial", None),
            ({"input_tokens": 7, "output_tokens": 9}, "reported", 16),
            ({"input_tokens": 7, "output_tokens": 9, "total_tokens": 12}, "inconsistent", None),
        ]
        for usage, expected, total in cases:
            with self.subTest(usage=usage):
                result = self.call(value=response(usage=usage))
                self.assertEqual(result["usage_state"], expected)
                self.assertEqual(result["usage_accounting"]["total_tokens"], total)
        for usage in (
                {"input_tokens": True, "output_tokens": 9},
                {"input_tokens": -1}, {"output_tokens": 1.5},
                {"input_tokens_details": {"cached_tokens": False}},
                {"output_tokens_details": []},
                {"input_tokens": 7, "prompt_tokens": 8}):
            with self.subTest(usage=usage), self.assertRaises(ProviderError) as caught:
                self.call(value=response(usage=usage))
            self.assertNotEqual(caught.exception.billing_state, "usage_reported")
        with self.assertRaises(ProviderError) as caught:
            self.call(value=response(usage={"input_tokens": 20, "prompt_tokens": 7,
                                           "output_tokens": 10, "total_tokens": 30}))
        self.assertEqual(caught.exception.partial_result["usage"]["input_tokens"], 20)
        self.assertEqual(caught.exception.partial_result["usage"]["prompt_tokens"], 7)
        self.assertGreaterEqual(
            caught.exception.partial_result["usage_accounting"]["reported_tokens_lower_bound"], 30)
        events = [
            {"type": "response.created", "response": response(status="in_progress", output=[])},
            delta("安全正文"), terminal(response(usage={
                "input_tokens": 2, "output_tokens": 3, "total_tokens": 5}))]
        result = self.parse(events)
        self.assertEqual(result["usage_state"], "inconsistent")
        self.assertEqual(result["usage_accounting"]["reported_tokens_lower_bound"], 16)

    def test_http_errors_and_redirects_are_not_retried_or_followed(self):
        for status in (302, 401, 429, 500):
            before = len(Handler.requests)
            with self.subTest(status=status), self.assertRaises(ProviderError) as caught:
                self.call(status=status, value={"error": SECRET},
                          config=self.config(api_key=SECRET))
            self.assertEqual(caught.exception.http_status, status)
            self.assertEqual(len(Handler.requests), before + 1)
            self.assertEqual(caught.exception.billing_state, "unknown")
            self.assertNotIn(SECRET, str(caught.exception))

    def test_cancel_before_send_and_after_first_chunk_preserve_accounting(self):
        cancel = threading.Event()
        cancel.set()
        before = len(Handler.requests)
        with self.assertRaises(ProviderCanceled) as caught:
            self.call(cancel_event=cancel)
        self.assertEqual(len(Handler.requests), before)
        self.assertEqual(caught.exception.billing_state, "not_dispatched")
        cancel.clear()
        with self.assertRaises(ProviderCanceled) as caught:
            self.call(pieces=[stream_bytes([delta("首段")]), 0.4,
                              stream_bytes([terminal(response("首段"))])],
                      content_type="text/event-stream", stream=True,
                      on_chunk=lambda _chunk: cancel.set(), cancel_event=cancel)
        self.assertEqual(caught.exception.partial_result["text"], "首段")
        self.assertFalse(caught.exception.partial_result["safe_to_apply"])
        self.assertEqual(caught.exception.billing_state, "unknown")

    def test_idle_total_and_size_limits_stop_without_automatic_retry(self):
        for options, pieces, expected in (
                ({"idle_timeout": 0.12}, [stream_bytes([delta("首段")]), 0.5], "timeout"),
                ({"total_timeout": 0.13},
                 [b": live\n\n", 0.05, b": live\n\n", 0.05, b": live\n\n", 0.2], "timeout"),
                ({"max_response_bytes": 80}, [encoded(response("x" * 1000))], "response_too_large"),
                ({}, [b"data: " + b"x" * 256001 + b"\n\n"], "response_too_large")):
            before = len(Handler.requests)
            with self.subTest(options=options), self.assertRaises(ProviderError) as caught:
                self.call(pieces=pieces, content_type="text/event-stream", stream=True,
                          config=self.config(**options))
            self.assertIn(caught.exception.code, {expected, "connection_interrupted"})
            self.assertEqual(len(Handler.requests), before + 1)
            self.assertFalse(caught.exception.partial_result["safe_to_apply"])

    def test_dispatch_or_chunk_persistence_failure_stops_request(self):
        def fail(_value):
            raise OSError("private disk content")
        before = len(Handler.requests)
        with self.assertRaises(ProviderError) as caught:
            self.call(on_dispatch=fail)
        self.assertEqual(caught.exception.code, "dispatch_audit_failed")
        self.assertEqual(len(Handler.requests), before)
        self.assertFalse(caught.exception.request_dispatched)
        with self.assertRaises(ProviderError) as caught:
            self.call(events=native_events(), stream=True, on_chunk=fail)
        self.assertEqual(caught.exception.code, "chunk_persistence_failed")
        self.assertTrue(caught.exception.request_dispatched)
        self.assertNotIn("private disk content", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
