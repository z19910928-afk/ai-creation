#!/usr/bin/env python3
"""Synthetic chat reply compatibility checks; no user data or network calls."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.moyu_agent_chat import SCHEMA
from backend.moyu_provider import (
    ProviderError, normalize_chat_reply, normalize_json_completion, parse_sse_completion,
)


REPLY = "先让人物面对一个具体选择，再讨论三种不同的代价。"
ACTION = {"kind": "open", "label": "查看规划", "instruction": "", "view": "blueprint"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def chat_response(text, finish="stop", **message):
    return {"model": "synthetic-chat", "choices": [
        {"message": {"role": "assistant", "content": text, **message}, "finish_reason": finish}]}


def responses_response(text):
    return {"id": "resp-synthetic", "object": "response", "model": "synthetic-chat",
            "status": "completed", "error": None, "incomplete_details": None,
            "output": [{"id": "msg-synthetic", "type": "message", "role": "assistant",
                        "status": "completed", "content": [
                            {"type": "output_text", "text": text, "annotations": []}]}]}


class ReplyCompatibilityTests(unittest.TestCase):
    def parse(self, text, *, protocol="chat_completions", stream=False, finish="stop",
              done=True, task="assistant_chat", schema=SCHEMA):
        if protocol == "responses":
            value = responses_response(text)
            if stream:
                raw = b"event: response.completed\ndata: " + encoded({
                    "type": "response.completed", "response": value}) + b"\n\n"
        else:
            value = chat_response(text, finish)
            if stream:
                chunks = [
                    {"choices": [{"delta": {"content": text[:5]}, "finish_reason": None}]},
                    {"choices": [{"delta": {"content": text[5:]}, "finish_reason": finish}]},
                ]
                raw = b"".join(b"data: " + encoded(chunk) + b"\n\n" for chunk in chunks)
                if done:
                    raw += b"data: [DONE]\n\n"
        if stream:
            return parse_sse_completion(io.BytesIO(raw), task, "json", schema=schema,
                                        api_protocol=protocol)
        return normalize_json_completion(value, task, "json", schema, api_protocol=protocol)

    def test_standard_and_fenced_json_work_on_four_transports(self):
        for protocol in ("chat_completions", "responses"):
            for stream in (False, True):
                for fence in (False, True):
                    with self.subTest(protocol=protocol, stream=stream, fence=fence):
                        text = json.dumps({"reply": REPLY, "actions": [ACTION]}, ensure_ascii=False)
                        if fence:
                            text = "\n```json\n" + text + "\n```\n"
                        result = self.parse(text, protocol=protocol, stream=stream)
                        self.assertTrue(result["safe_to_apply"])
                        self.assertEqual(result["structured"], {"reply": REPLY, "actions": [ACTION]})
                        self.assertEqual(result["text"], text)

    def test_complete_natural_language_does_not_invent_actions(self):
        for protocol in ("chat_completions", "responses"):
            for stream in (False, True):
                with self.subTest(protocol=protocol, stream=stream):
                    result = self.parse(REPLY + " 可以讨论续写，不能删除原稿。",
                                        protocol=protocol, stream=stream)
                    self.assertTrue(result["safe_to_apply"])
                    self.assertEqual(result["structured"]["actions"], [])
                    self.assertIn(REPLY, result["structured"]["reply"])

    def test_raw_line_breaks_and_tabs_work_on_four_transports_without_rewriting_evidence(self):
        for control in ("\n", "\r", "\t", "\r\n"):
            reply = REPLY + control + '人物说："我选择留下。"'
            action = {**ACTION, "instruction": "先讨论。" + control + "不要直接修改。"}
            raw = json.dumps({"reply": reply, "actions": [action]}, ensure_ascii=False)
            raw = raw.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
            with self.assertRaises(json.JSONDecodeError):
                json.loads(raw)
            for protocol in ("chat_completions", "responses"):
                for stream in (False, True):
                    with self.subTest(control=repr(control), protocol=protocol, stream=stream):
                        result = self.parse(raw, protocol=protocol, stream=stream)
                        self.assertTrue(result["safe_to_apply"])
                        self.assertEqual(result["completion_state"], "complete")
                        self.assertEqual(result["structured"], {"reply": reply, "actions": [action]})
                        self.assertEqual(result["text"], raw)
                        self.assertEqual("".join(chunk["text"] for chunk in result["_chunks"]), raw)
                        self.assertEqual(normalize_chat_reply(
                            "```json\n" + raw + "\n```", SCHEMA), result["structured"])

    def test_other_raw_control_characters_are_rejected_in_reply_action_and_edges(self):
        for code in range(32):
            if code in (9, 10, 13):
                continue
            control = chr(code)
            samples = [
                '{"reply":"正文' + control + '","actions":[]}',
                '{"reply":"正文","actions":[{"kind":"open","label":"查看' + control
                + '","instruction":"","view":"blueprint"}]}',
                control + REPLY, REPLY + control,
            ]
            for raw in samples:
                with self.subTest(code=code, location=samples.index(raw)), self.assertRaises(ProviderError):
                    normalize_chat_reply(raw, SCHEMA)

    def test_multiline_tolerance_does_not_repair_syntax_or_allow_nonfinite_numbers(self):
        for raw in (
            '{"reply":"第一行\n第二行","actions":[],}',
            '{"reply":"第一行\n"坏引号"","actions":[]}',
            '{"reply":"第一行\n第二行","actions":[]',
            '{"reply":"第一行\n第二行","actions":[]} {"reply":"另一条"}',
            '{"reply":"第一行\n第二行","actions":[],"unused":NaN}',
            '{"reply":"第一行\n第二行","actions":[],"unused":Infinity}',
        ):
            with self.subTest(raw=repr(raw)), self.assertRaises(ProviderError) as caught:
                normalize_chat_reply(raw, SCHEMA)
            self.assertEqual(caught.exception.code, "invalid_structured_output")
            self.assertIn("总管回复", str(caught.exception))

    def test_multiline_tolerance_keeps_cancel_truncation_and_transport_boundaries(self):
        raw = '{"reply":"第一行\n第二行","actions":[]}'
        for finish in ("length", "content_filter", "tool_calls", None):
            with self.subTest(finish=finish):
                result = self.parse(raw, stream=True, finish=finish)
                self.assertFalse(result["safe_to_apply"])
                self.assertIsNone(result["structured"])
        result = self.parse(raw, stream=True, done=False)
        self.assertFalse(result["safe_to_apply"])
        self.assertIsNone(result["structured"])
        with self.assertRaises(ProviderError):
            self.parse(raw, task="continue", schema={
                "type": "object", "required": ["reply"],
                "properties": {"reply": {"type": "string"}}})
        with self.assertRaises(ProviderError) as caught:
            parse_sse_completion(io.BytesIO(
                b'data: {"choices":[{"delta":{"content":"line\tbreak"}}]}\n\n'),
                "assistant_chat", "json", schema=SCHEMA)
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_missing_actions_and_extra_metadata_preserve_reply(self):
        self.assertEqual(normalize_chat_reply(json.dumps({
            "reply": REPLY, "unused": {"kind": "delete"}}, ensure_ascii=False), SCHEMA),
            {"reply": REPLY, "actions": []})

    def test_invalid_actions_are_dropped_individually(self):
        invalid = [{"kind": "delete", "label": "删除", "instruction": "删除原稿。"},
                   {"kind": "open", "label": "打开", "instruction": "", "view": "admin"},
                   {"kind": "continue", "label": "", "instruction": "续写。"}, None, "执行"]
        for action in invalid:
            with self.subTest(action=action):
                result = normalize_chat_reply(json.dumps(
                    {"reply": REPLY, "actions": [action, ACTION]}, ensure_ascii=False), SCHEMA)
                self.assertEqual(result, {"reply": REPLY, "actions": [ACTION]})

    def test_actions_with_wrong_container_never_execute(self):
        for actions in (None, "delete", {}, True):
            with self.subTest(actions=actions):
                result = normalize_chat_reply(json.dumps(
                    {"reply": REPLY, "actions": actions}, ensure_ascii=False), SCHEMA)
                self.assertEqual(result["actions"], [])

    def test_action_count_and_individual_payload_are_bounded(self):
        result = normalize_chat_reply(json.dumps({"reply": REPLY, "actions": [ACTION] * 80}),
                                      SCHEMA)
        self.assertEqual(len(result["actions"]), 4)
        huge = {**ACTION, "instruction": "x" * 2001}
        result = normalize_chat_reply(json.dumps({"reply": REPLY, "actions": [huge]}), SCHEMA)
        self.assertEqual(result["actions"], [])

    def test_broken_or_ambiguous_json_cannot_become_prose(self):
        for text in ('{"reply":"半句', '[{"reply":"正文"}]',
                     '{"reply":"正文"}\n{"actions":[]}',
                     '这是结果：\n```json\n{"reply":"正文","actions":[]}\n```',
                     '这是结果：\n```JSON\n坏格式\n```',
                     '这是结果： {"reply":"正文","actions":[]}',
                     '```json\n坏格式\n```', "null", "true", "12"):
            with self.subTest(text=text), self.assertRaises(ProviderError):
                normalize_chat_reply(text, SCHEMA)

    def test_empty_nonstring_and_overlong_reply_are_rejected(self):
        for value in ("", " \n", [], None, "x" * 16001):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ProviderError):
                normalize_chat_reply(json.dumps({"reply": value, "actions": []}), SCHEMA)
        with self.assertRaises(ProviderError):
            normalize_chat_reply(" \n", SCHEMA)

    def test_thinking_markers_are_not_recovered_as_chat(self):
        for text in ("<think>内部思考</think>回复", "<think>未结束",
                     "<analysis>内部分析</analysis>", "回复\n<THINK>内容</THINK>",
                     r'{"reply":"\u003cthink\u003ehidden\u003c/think\u003e","actions":[]}'):
            with self.subTest(text=text), self.assertRaises(ProviderError):
                normalize_chat_reply(text, SCHEMA)

    def test_decoded_action_fields_cannot_leak_thinking_into_saved_materials(self):
        inspiration = {"kind": "save_inspiration", "label": "保存灵感",
                       "instruction": "保留方案", "title": "修钟学徒",
                       "content": "学徒必须在亲人与城市记忆之间作选择。"}
        for field in ("label", "instruction", "title", "content"):
            for marker in ("think", "ANALYSIS"):
                for escaped in (False, True):
                    with self.subTest(field=field, marker=marker, escaped=escaped):
                        action = {**inspiration, field: f"<{marker}>hidden</{marker}>"}
                        raw = json.dumps({"reply": REPLY, "actions": [action, ACTION]})
                        if escaped:
                            raw = raw.replace("<", r"\u003c").replace(">", r"\u003e")
                        result = normalize_chat_reply(raw, SCHEMA)
                        self.assertEqual(result, {"reply": REPLY, "actions": [ACTION]})
        action = {**ACTION, "instruction": "<think>hidden</think>"}
        raw = json.dumps({"reply": REPLY, "actions": [action]}).replace("<", r"\u003c")
        self.assertEqual(normalize_chat_reply(raw, SCHEMA), {"reply": REPLY, "actions": []})

    def test_length_filter_and_unknown_finish_never_promote_actions(self):
        text = json.dumps({"reply": REPLY, "actions": [ACTION]})
        for finish in ("length", "content_filter", "tool_calls", None):
            with self.subTest(finish=finish):
                result = self.parse(text, stream=True, finish=finish)
                self.assertFalse(result["safe_to_apply"])
                self.assertIsNone(result["structured"])

    def test_missing_done_marker_never_promotes_plain_reply(self):
        result = self.parse(REPLY, stream=True, done=False)
        self.assertFalse(result["safe_to_apply"])
        self.assertIsNone(result["structured"])

    def test_nonchat_tasks_keep_strict_json_contract(self):
        for text in (REPLY, '```json\n{"content":"正文"}\n```'):
            with self.subTest(text=text), self.assertRaises(ProviderError):
                self.parse(text, task="continue", schema={
                    "type": "object", "required": ["content"],
                    "properties": {"content": {"type": "string"}}})

    def test_outer_protocol_json_remains_strict(self):
        with self.assertRaises(ProviderError) as caught:
            parse_sse_completion(io.BytesIO(b"data: broken json\n\n"),
                                 "assistant_chat", "json", schema=SCHEMA)
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_separate_reasoning_fields_never_enter_reply(self):
        hidden = "synthetic-private-reasoning"
        result = normalize_json_completion(
            chat_response(REPLY, reasoning_content=hidden),
            "assistant_chat", "json", SCHEMA)
        self.assertEqual(result["structured"], {"reply": REPLY, "actions": []})
        self.assertNotIn(hidden, json.dumps(result))
        value = responses_response(REPLY)
        value["output"].insert(0, {"id": "rs-synthetic", "type": "reasoning",
                                  "summary": [{"type": "summary_text", "text": hidden}]})
        result = normalize_json_completion(value, "assistant_chat", "json", SCHEMA,
                                           api_protocol="responses")
        self.assertNotIn(hidden, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
