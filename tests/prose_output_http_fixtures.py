#!/usr/bin/env python3
"""Actual HTTP prose contracts using an isolated DB and free local provider."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import server  # noqa: E402
from backend import moyu_revision  # noqa: E402


SELECTED = "😀她把铜环留在桌上。 \n\t"
BODY = "😀潮声停了。\n" + SELECTED + "\n窗沿的雨水滴在地板上，她听见了门外的脚步。"
UNICODE_PROSE = "😀她将铜环留在桌上。\n\t"


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class LocalProseProvider(BaseHTTPRequestHandler):
    text = ""
    reasoning = None
    reasoning_field = "reasoning_content"
    incomplete = False
    calls = []
    lock = threading.Lock()

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with self.lock:
            self.calls.append(payload)
            text = self.text
            reasoning = self.reasoning
            reasoning_field = self.reasoning_field
            incomplete = self.incomplete
        if incomplete:
            body = ("data: " + json.dumps({
                "model": "local-prose-contract",
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
            }, ensure_ascii=False) + "\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if (payload.get("response_format") or {}).get("type") in {"json_object", "json_schema"}:
            text = json.dumps({"content": text}, ensure_ascii=False)
        message = {"content": text}
        if reasoning is not None:
            message[reasoning_field] = reasoning
        body = encode({
            "model": "local-prose-contract",
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class ProseOutputHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db = server.DB_PATH
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), LocalProseProvider)
        cls.provider.daemon_threads = True
        cls.provider_thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.provider_thread.start()
        cls.provider_base = f"http://127.0.0.1:{cls.provider.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.provider_thread.join(timeout=3)
        server.DB_PATH = cls.original_db

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="moyu-prose-output-http-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_AI_DAILY_QUOTA": "0", "MOYU_CREDENTIALS_DIR": str(self.root / "private-keys"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        server.DB_PATH = self.root / "book.sqlite3"
        server.init_db()
        self.project = server.PROJECT_ID
        self.branch = server.BRANCH_ID
        with LocalProseProvider.lock:
            LocalProseProvider.calls.clear()
            LocalProseProvider.text = ""
            LocalProseProvider.reasoning = None
            LocalProseProvider.reasoning_field = "reasoning_content"
            LocalProseProvider.incomplete = False

        class AppHandler(server.Handler):
            def log_message(self, *_args):
                return

        self.app = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        self.app.daemon_threads = True
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_app)
        self.base = f"http://127.0.0.1:{self.app.server_port}"
        self.chapter_id = self.state()["chapters"][0]["id"]
        status, changed = self.request("PATCH", "/api/chapters/" + self.chapter_id, {
            "body": BODY, "pov": "全知视角", "chapter_goal": "留下铜环", "scene_plan": "留下铜环后听见脚步",
        })
        self.assertEqual(status, 200, changed)
        status, saved = self.request("PUT", "/api/ai/settings", {
            "base_url": self.provider_base, "model": "local-prose-contract", "api_key": "",
            "daily_limit": 20, "parameters": {"temperature": 0, "max_tokens": 3000},
            "styles": {"tone": "克制", "instructions": "保留人物已有事实"},
        })
        self.assertEqual(status, 200, saved)

    def close_app(self):
        self.app.shutdown()
        self.app.server_close()
        self.thread.join(timeout=3)

    def request(self, method, route, payload=None):
        request = urllib.request.Request(
            self.base + route, method=method,
            data=encode(payload) if payload is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Moyu-Project": self.project,
                "X-Moyu-Branch": self.branch,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def state(self):
        status, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200, state)
        return state

    def manuscript_snapshot(self):
        state = self.state()
        with server.connect() as conn:
            revisions = conn.execute("SELECT COUNT(*) FROM document_revision").fetchone()[0]
            applications = conn.execute("SELECT COUNT(*) FROM app_candidate_application").fetchone()[0]
            decisions = tuple(tuple(row) for row in conn.execute(
                "SELECT id,author_decision FROM ai_run ORDER BY rowid"))
        return state["chapters"], state["head_revision"], revisions, applications, decisions

    def source(self, task):
        chapter = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        start = len(chapter["body"])
        end = start
        mode = "append"
        if task == "polish":
            start = chapter["body"].index(SELECTED)
            end = start + len(SELECTED)
            mode = "replace"
        return {
            "project_id": self.project, "branch_id": self.branch, "chapter_id": self.chapter_id,
            "source_revision_id": chapter["revision_id"], "source_body_hash": server.hash_text(chapter["body"]),
            "mode": mode, "range": {"start": start, "end": end, "quote": chapter["body"][start:end]},
        }

    def enqueue(self, text, *, task="continue", instruction="续写正文，控制在10至16字。",
                response_format="text"):
        with LocalProseProvider.lock:
            LocalProseProvider.text = text
        source = self.source(task)
        status, job = self.request("POST", "/api/ai/queue", {
            "task": task, "chapter_id": self.chapter_id, "instruction": instruction,
            "source": source, "defer": True, "response_format": response_format,
        })
        self.assertEqual(status, 202, job)
        self.assertEqual(job["source"], source)
        return job

    def process(self, job):
        status, completed = self.request("POST", f"/api/ai/jobs/{job['id']}/process", {})
        self.assertEqual(status, 200, completed)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(completed["result"]["completion_state"], "complete")
        self.assertTrue(completed["result"]["safe_to_apply"])
        return completed

    def frozen_contract(self, job):
        with server.connect() as conn:
            row = conn.execute("SELECT context_json FROM app_ai_job WHERE id=?", (job["id"],)).fetchone()
        return json.loads(row[0])["_output_contract"]

    def application(self, job, text, *, decision="accepted", **extra):
        replacement = "\n\n" + text if job["source"]["mode"] == "append" else text
        return {
            **copy.deepcopy(job["source"]), "replacement": replacement,
            "candidate_hash": server.hash_text(text), "decision": decision,
            "expected_head": self.state()["head_revision"], "reason": "作者已逐字整理并查看差异",
            **extra,
        }

    def assert_rejected_without_changes(self, job, text, *, decision="accepted", **extra):
        snapshot = self.manuscript_snapshot()
        with LocalProseProvider.lock:
            calls = len(LocalProseProvider.calls)
        status, rejected = self.request(
            "POST", f"/api/ai/runs/{job['ai_run_id']}/apply",
            self.application(job, text, decision=decision, **extra),
        )
        self.assertEqual(status, 400, rejected)
        self.assertEqual(self.manuscript_snapshot(), snapshot)
        with LocalProseProvider.lock:
            self.assertEqual(len(LocalProseProvider.calls), calls)
        return rejected

    def revision_request(self, job, text, *, request_id="fixture-revision", notes="减少重复描写"):
        return {
            "request_id": request_id, "candidate_hash": server.hash_text(text),
            "notes": notes, "defer": True,
        }

    def revision_counts(self):
        with server.connect() as conn:
            return tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                         for table in ("app_ai_job", "app_candidate_revision",
                                       "app_quota_reservation"))

    def assert_revision_rejected(self, job, payload, *, expected_status=400):
        before = self.revision_counts()
        snapshot = self.manuscript_snapshot()
        with LocalProseProvider.lock:
            calls = len(LocalProseProvider.calls)
        status, rejected = self.request(
            "POST", f"/api/ai/runs/{job['ai_run_id']}/revise", payload,
        )
        self.assertEqual(status, expected_status, rejected)
        self.assertEqual(self.revision_counts(), before)
        self.assertEqual(self.manuscript_snapshot(), snapshot)
        with LocalProseProvider.lock:
            self.assertEqual(len(LocalProseProvider.calls), calls)
        return rejected

    def test_complete_oversize_is_retained_but_accepted_cannot_change_manuscript(self):
        text = "潮" * 18
        job = self.enqueue(text)
        completed = self.process(job)
        result = completed["result"]
        self.assertEqual(result["text"], text)
        self.assertFalse(result["output_validation"]["eligible"])
        self.assertEqual(result["output_validation"]["characters"], 18)
        self.assertIn("length_out_of_range", {issue["code"] for issue in result["output_validation"]["issues"]})
        rejected = self.assert_rejected_without_changes(job, text)
        self.assertIn("10至16", rejected["error"])
        status, stored = self.request("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, stored)
        self.assertEqual(stored["result"]["text"], text)
        self.assertEqual(stored["author_decision"], "pending")

    def test_complete_one_character_stop_is_retained_but_not_adoptable(self):
        text = "铁"
        job = self.enqueue(text, instruction="续写正文，控制在600至850字。")
        completed = self.process(job)
        result = completed["result"]
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["completion_state"], "complete")
        self.assertTrue(result["safe_to_apply"])
        validation = result["output_validation"]
        self.assertEqual(validation["characters"], 1)
        self.assertFalse(validation["eligible"])
        self.assertIn("length_out_of_range", {issue["code"] for issue in validation["issues"]})
        rejected = self.assert_rejected_without_changes(job, text)
        self.assertIn("1字", rejected["error"])
        status, stored = self.request("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, stored)
        self.assertEqual(stored["result"]["text"], text)
        self.assertEqual(stored["author_decision"], "pending")

    def test_disabled_thinking_mismatch_blocks_application_and_revision_without_retry(self):
        status, settings = self.request("PUT", "/api/ai/settings", {
            "parameters": {"temperature": 0, "max_tokens": 3000,
                           "thinking": {"type": "disabled"}},
        })
        self.assertEqual(status, 200, settings)
        private_reasoning = "私密思考文本不得进入数据库或网页"
        for text in ("铁", "雨" * 12):
            with self.subTest(characters=len(text)):
                with LocalProseProvider.lock:
                    LocalProseProvider.reasoning = private_reasoning
                job = self.enqueue(text)
                completed = self.process(job)
                result = completed["result"]
                self.assertEqual(result["text"], text)
                self.assertEqual(result["finish_reason"], "stop")
                self.assertEqual(result["completion_state"], "complete")
                self.assertTrue(result["safe_to_apply"])
                self.assertEqual(result["usage"]["total_tokens"], 60)
                self.assertEqual(result["reasoning_observation"], {
                    "field_events": 1, "nonempty_events": 1, "invalid_events": 0,
                    "characters": len(private_reasoning),
                    "requested_disabled": True, "unexpected_reasoning": True,
                })
                validation = result["output_validation"]
                self.assertFalse(validation["eligible"])
                self.assertFalse(validation["eligible_for_blind_review"])
                self.assertTrue(validation["protocol_complete"])
                self.assertIn("reasoning_content_observed",
                              {issue["code"] for issue in validation["issues"]})
                self.assertNotIn(private_reasoning, json.dumps(completed, ensure_ascii=False))
                for body, decision in ((text, "accepted"), ("雨" * 12, "edited")):
                    rejected = self.assert_rejected_without_changes(job, body, decision=decision)
                    self.assertIn("回复与关闭思考设置不一致", rejected["error"])
                rejected = self.assert_revision_rejected(job, self.revision_request(job, text))
                self.assertIn("回复与关闭思考设置不一致", rejected["error"])
                self.process(job)
                status, stored = self.request("GET", "/api/ai/jobs/" + job["id"])
                self.assertEqual(status, 200, stored)
                self.assertEqual(stored["result"], result)
                self.assertEqual(stored["author_decision"], "pending")
                with server.connect() as conn:
                    for table, field in (("app_ai_job", "result_json"), ("app_ai_chunk", "text"),
                                         ("ai_run", "consent_json")):
                        self.assertTrue(all(private_reasoning not in (row[0] or "")
                                            for row in conn.execute(f"SELECT {field} FROM {table}")))
        with LocalProseProvider.lock:
            self.assertEqual(len(LocalProseProvider.calls), 2)

    def test_reasoning_alias_and_unknown_shapes_cannot_bypass_good_prose_with_manual_edits(self):
        status, settings = self.request("PUT", "/api/ai/settings", {
            "parameters": {"temperature": 0, "max_tokens": 3000,
                           "thinking": {"type": "disabled"}},
        })
        self.assertEqual(status, 200, settings)
        private = "私密字段文字不可保存"
        text = "雨" * 725
        for field, value in (("reasoning", private), ("reasoning_content", {"text": private}),
                             ("reasoning", []), ("reasoning_content", False)):
            for response_format in ("text", "json"):
                with self.subTest(field=field, kind=type(value).__name__, format=response_format):
                    with LocalProseProvider.lock:
                        LocalProseProvider.reasoning = value
                        LocalProseProvider.reasoning_field = field
                    job = self.enqueue(text, instruction="续写600至850字。",
                                       response_format=response_format)
                    result = self.process(job)["result"]
                    self.assertEqual(result["finish_reason"], "stop")
                    self.assertTrue(result["safe_to_apply"])
                    self.assertTrue(result["output_validation"]["protocol_complete"])
                    self.assertEqual(result["output_validation"]["characters"], 725)
                    self.assertFalse(result["output_validation"]["eligible"])
                    self.assertFalse(result["output_validation"]["eligible_for_blind_review"])
                    expected_invalid = int(not isinstance(value, str))
                    self.assertEqual(result["reasoning_observation"]["invalid_events"], expected_invalid)
                    self.assertEqual(result["reasoning_observation"]["nonempty_events"], 1 - expected_invalid)
                    issue = result["output_validation"]["issues"][-1]
                    self.assertEqual(issue["code"], "reasoning_content_observed")
                    if expected_invalid:
                        self.assertIn("思考字段类型无法校验", issue["description"])
                        self.assertNotIn("非空", issue["description"])
                    self.assertNotIn(private, json.dumps(result, ensure_ascii=False))
                    for body, decision in ((text, "accepted"), ("潮" * 725, "edited")):
                        self.assert_rejected_without_changes(job, body, decision=decision)
                    self.assert_revision_rejected(job, self.revision_request(job, text))
                    with server.connect() as conn:
                        for table, column in (("app_ai_job", "result_json"), ("app_ai_chunk", "text"),
                                              ("ai_run", "consent_json")):
                            self.assertTrue(all(private not in (row[0] or "")
                                                for row in conn.execute(f"SELECT {column} FROM {table}")))
        with LocalProseProvider.lock:
            self.assertEqual(len(LocalProseProvider.calls), 8)

    def test_provider_issue_checks_old_alias_or_invalid_shapes_without_rewriting_record(self):
        result = {
            "request_audit": {"effective_parameters": {"thinking": {"type": "disabled"}}},
            "reasoning_observation": {"nonempty_events": 0, "requested_disabled": True},
            "protocol_observation": {"events": [{"choices": [{
                "message": {"fields": {}},
            }]}]},
        }
        fields = result["protocol_observation"]["events"][0]["choices"][0]["message"]["fields"]
        for field, shape in (("reasoning", {"type": "string", "chars": 7}),
                             ("reasoning_content", {"type": "object", "fields": 0})):
            with self.subTest(field=field):
                fields.clear()
                fields[field] = shape
                before = copy.deepcopy(result)
                self.assertEqual(server.prose_provider_issue(result)["code"], "reasoning_content_observed")
                self.assertEqual(result, before)
        fields.clear()
        fields["reasoning_content"] = {"type": "string", "chars": 2}
        self.assertIsNone(server.prose_provider_issue(result))
        result["reasoning_observation"] = {"nonempty_events": False, "invalid_events": 0}
        self.assertIsNotNone(server.prose_provider_issue(result))

    def test_edited_oversize_and_planning_still_fail_without_side_effects(self):
        job = self.enqueue("雨" * 12)
        completed = self.process(job)
        self.assertTrue(completed["result"]["output_validation"]["eligible"])
        self.assert_rejected_without_changes(job, "雨" * 17, decision="edited")
        for text in ("思考过程：\n她留在门外。", "<think>\n她留在门外。", "Analysis:\n她留在门外。"):
            with self.subTest(text=text):
                self.assertTrue(10 <= sum(not char.isspace() for char in text) <= 16)
                rejected = self.assert_rejected_without_changes(job, text, decision="edited")
                self.assertIn("规划或思考标记", rejected["error"])

    def test_manual_unicode_cleanup_can_be_applied_once_with_original_contract(self):
        job = self.enqueue("雨" * 12, instruction="续写正文10字。")
        completed = self.process(job)
        self.assertFalse(completed["result"]["output_validation"]["eligible"])
        self.assertEqual(sum(not char.isspace() for char in UNICODE_PROSE), 10)
        payload = self.application(job, UNICODE_PROSE, decision="edited")
        status, applied = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/apply", payload)
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])
        changed = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.assertEqual(changed["body"], BODY + "\n\n" + UNICODE_PROSE)
        status, detail = self.request("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["author_decision"], "edited")
        self.assertEqual(detail["result"]["text"], "雨" * 12)
        snapshot = self.manuscript_snapshot()
        status, repeated = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/apply", payload)
        self.assertEqual(status, 200, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.manuscript_snapshot(), snapshot)

    def test_polish_relative_contract_uses_exact_selected_unicode_prose(self):
        instruction = "润色正文，保持原文的80%至120%。"
        job = self.enqueue("雨" * 13, task="polish", instruction=instruction)
        contract = self.frozen_contract(job)
        self.assertEqual(contract["source_scope"], "selection")
        self.assertEqual(contract["source_characters"], 10)
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (8, 12))
        self.assertGreater(len(SELECTED), contract["source_characters"])
        self.assertGreater(sum(not char.isspace() for char in BODY), contract["source_characters"])
        completed = self.process(job)
        self.assertEqual(completed["result"]["output_validation"]["contract"], contract)
        self.assert_rejected_without_changes(job, "雨" * 13)
        with LocalProseProvider.lock:
            user = LocalProseProvider.calls[-1]["messages"][-1]["content"]
        context = json.loads(user.split("<manuscript>\n", 1)[1].split("\n</manuscript>", 1)[0])
        self.assertEqual(context["current_chapter"]["body"], SELECTED)
        self.assertEqual(context["current_chapter"]["source_range"], {
            "start": BODY.index(SELECTED), "end": BODY.index(SELECTED) + len(SELECTED),
        })
        status, applied = self.request(
            "POST", f"/api/ai/runs/{job['ai_run_id']}/apply",
            self.application(job, UNICODE_PROSE, decision="edited"),
        )
        self.assertEqual(status, 200, applied)
        selected = job["source"]["range"]
        current = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.assertEqual(current["body"], BODY[:selected["start"]] + UNICODE_PROSE + BODY[selected["end"]:])
        self.assertEqual(self.frozen_contract(job), contract)

    def test_queued_settings_changes_do_not_change_frozen_contract_or_prompt(self):
        job = self.enqueue("雨" * 12)
        contract = self.frozen_contract(job)
        status, changed = self.request("PUT", "/api/ai/settings", {
            "model": "new-local-model",
            "styles": {"tone": "强烈", "instructions": "续写正文不少于500字"},
            "parameters": {"temperature": 0.8, "max_tokens": 5000},
        })
        self.assertEqual(status, 200, changed)
        completed = self.process(job)
        self.assertEqual(self.frozen_contract(job), contract)
        self.assertEqual(completed["result"]["output_validation"]["contract"], contract)
        with LocalProseProvider.lock:
            request = LocalProseProvider.calls[-1]
        self.assertEqual(request["model"], "local-prose-contract")
        self.assertEqual(request["max_tokens"], 3000)
        self.assertEqual(request["temperature"], 0)
        self.assertIn("保留人物已有事实", request["messages"][0]["content"])
        self.assertNotIn("500字", request["messages"][0]["content"])
        self.assert_rejected_without_changes(job, "雨" * 17, decision="edited")
        status, applied = self.request(
            "POST", f"/api/ai/runs/{job['ai_run_id']}/apply", self.application(job, "雨" * 12),
        )
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])

    def test_later_request_and_application_fields_cannot_expand_old_contract(self):
        old = self.enqueue("雨" * 18)
        self.process(old)
        contract = self.frozen_contract(old)
        later = self.enqueue("雨" * 18, instruction="续写正文，控制在10至24字。")
        self.process(later)
        self.assertEqual(self.frozen_contract(later)["maximum_characters"], 24)
        rejected = self.assert_rejected_without_changes(
            old, "雨" * 18, decision="edited", instruction="续写正文，控制在10至24字。",
            output_contract={"minimum_characters": 1, "maximum_characters": 1000},
        )
        self.assertIn("10至16", rejected["error"])
        self.assertEqual(self.frozen_contract(old), contract)
        status, applied = self.request(
            "POST", f"/api/ai/runs/{later['ai_run_id']}/apply", self.application(later, "雨" * 18),
        )
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])

    def test_json_prose_applies_only_content_and_preserves_transport_record(self):
        job = self.enqueue(UNICODE_PROSE, instruction="续写正文10字。", response_format="json")
        completed = self.process(job)
        result = completed["result"]
        self.assertEqual(result["structured"]["content"], UNICODE_PROSE)
        self.assertEqual(json.loads(result["text"])["content"], UNICODE_PROSE)
        self.assertNotEqual(result["text"], UNICODE_PROSE)
        self.assertEqual(result["output_validation"]["characters"], 10)
        self.assertTrue(result["output_validation"]["eligible"])
        self.assert_rejected_without_changes(job, result["text"])
        status, applied = self.request(
            "POST", f"/api/ai/runs/{job['ai_run_id']}/apply", self.application(job, UNICODE_PROSE),
        )
        self.assertEqual(status, 200, applied)
        current = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.assertEqual(current["body"], BODY + "\n\n" + UNICODE_PROSE)
        self.assertNotIn('{"content"', current["body"])
        status, stored = self.request("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, stored)
        self.assertEqual(stored["result"]["text"], result["text"])

    def test_json_oversize_content_is_rechecked_on_accepted_and_edited_application(self):
        text = "潮" * 18
        job = self.enqueue(text, response_format="json")
        completed = self.process(job)
        self.assertEqual(completed["result"]["structured"]["content"], text)
        validation = completed["result"]["output_validation"]
        self.assertEqual(validation["characters"], 18)
        self.assertFalse(validation["eligible"])
        for decision in ("accepted", "edited"):
            with self.subTest(decision=decision):
                rejected = self.assert_rejected_without_changes(job, text, decision=decision)
                self.assertIn("10至16", rejected["error"])

    def test_json_planning_content_is_blocked_until_author_cleans_the_body(self):
        text = "思考过程：\n她留在门外。"
        job = self.enqueue(text, response_format="json")
        completed = self.process(job)
        issues = completed["result"]["output_validation"]["issues"]
        self.assertIn("planning_contamination", {issue["code"] for issue in issues})
        self.assert_rejected_without_changes(job, text)
        self.assert_rejected_without_changes(job, text + " ", decision="edited")
        status, applied = self.request(
            "POST", f"/api/ai/runs/{job['ai_run_id']}/apply",
            self.application(job, UNICODE_PROSE, decision="edited"),
        )
        self.assertEqual(status, 200, applied)
        current = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.assertEqual(current["body"], BODY + "\n\n" + UNICODE_PROSE)
        self.assertNotIn("思考过程", current["body"])

    def test_oversize_revision_preserves_full_parent_and_applies_one_child_once(self):
        text = "潮" * 18 + "\n\t"
        parent = self.enqueue(text)
        original = self.process(parent)
        self.assertFalse(original["result"]["output_validation"]["eligible"])
        with LocalProseProvider.lock:
            parent_payload = copy.deepcopy(LocalProseProvider.calls[0])
        with server.connect() as conn:
            original_run = dict(conn.execute(
                "SELECT output_hash,token_json,cost_json FROM ai_run WHERE id=?",
                (parent["ai_run_id"],)).fetchone())
            original_job = conn.execute(
                "SELECT result_json FROM app_ai_job WHERE id=?", (parent["id"],)).fetchone()[0]
        contract = self.frozen_contract(parent)
        body_before = self.state()["chapters"]
        request = self.revision_request(parent, text)
        status, created = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise", request,
        )
        self.assertEqual(status, 202, created)
        self.assertFalse(created["idempotent"])
        child = created["job"]
        self.assertEqual(child["source"], parent["source"])
        self.assertEqual(self.frozen_contract(child), contract)
        binding = child["candidate_revision"]
        self.assertEqual(binding, {
            "operation": "revise_candidate", "parent_run_id": parent["ai_run_id"],
            "root_run_id": parent["ai_run_id"], "parent_characters": 18,
        })
        self.assertEqual(self.state()["chapters"], body_before)
        self.assertEqual(self.revision_counts(), (2, 1, 2))
        with LocalProseProvider.lock:
            self.assertEqual(len(LocalProseProvider.calls), 1)
        status, repeated = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise", request,
        )
        self.assertEqual(status, 202, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["job"]["id"], child["id"])
        self.assertEqual(repeated["job"]["candidate_revision"], binding)
        self.assertEqual(self.revision_counts(), (2, 1, 2))
        changed_notes = {**request, "notes": "另加结尾"}
        self.assert_revision_rejected(parent, changed_notes, expected_status=409)
        with LocalProseProvider.lock:
            LocalProseProvider.text = "雨" * 12
        completed = self.process(child)
        self.assertEqual(completed["candidate_revision"], binding)
        self.assertEqual(completed["result"]["candidate_revision"], binding)
        self.assertTrue(completed["result"]["output_validation"]["eligible"])
        self.assertEqual(completed["result"]["output_validation"]["contract"], contract)
        with LocalProseProvider.lock:
            self.assertEqual(len(LocalProseProvider.calls), 2)
            child_payload = copy.deepcopy(LocalProseProvider.calls[-1])
        self.assertEqual(
            {key: value for key, value in parent_payload.items() if key != "messages"},
            {key: value for key, value in child_payload.items() if key != "messages"},
        )
        self.assertEqual(child_payload["messages"][1:-1], parent_payload["messages"][1:])
        self.assertIn(parent_payload["messages"][0]["content"],
                      child_payload["messages"][0]["content"])
        material = json.loads(child_payload["messages"][-1]["content"])
        self.assertEqual(material["candidate_text"], text)
        self.assertFalse(material["candidate_adopted"])
        self.assertEqual(material["actual_characters"], 18)
        self.assertEqual(material["output_contract"], contract)
        self.assertEqual(material["author_notes"], request["notes"])
        status, after_completed = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise", request,
        )
        self.assertEqual(status, 202, after_completed)
        self.assertTrue(after_completed["idempotent"])
        self.process(child)
        self.assertEqual(self.revision_counts(), (2, 1, 2))
        with LocalProseProvider.lock:
            self.assertEqual(len(LocalProseProvider.calls), 2)
        status, read_child = self.request("GET", "/api/ai/jobs/" + child["id"])
        self.assertEqual(status, 200, read_child)
        self.assertEqual(read_child["candidate_revision"], binding)
        self.assertNotIn("notes", binding)
        self.assertNotIn("candidate_text", binding)
        with server.connect() as conn:
            self.assertEqual(dict(conn.execute(
                "SELECT output_hash,token_json,cost_json FROM ai_run WHERE id=?",
                (parent["ai_run_id"],)).fetchone()), original_run)
            self.assertEqual(conn.execute(
                "SELECT result_json FROM app_ai_job WHERE id=?",
                (parent["id"],)).fetchone()[0], original_job)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_candidate_revision WHERE parent_run_id=?",
                (parent["ai_run_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_ai_job WHERE id=?", (child["id"],)).fetchone()[0], 1)
        status, read_parent = self.request("GET", "/api/ai/jobs/" + parent["id"])
        self.assertEqual(status, 200, read_parent)
        self.assertEqual(read_parent["result"]["text"], text)
        self.assertEqual(read_parent["author_decision"], "pending")
        status, applied = self.request(
            "POST", f"/api/ai/runs/{child['ai_run_id']}/apply",
            self.application(child, "雨" * 12),
        )
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])
        current = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.assertEqual(current["body"], BODY + "\n\n" + "雨" * 12)

    def test_json_polish_revision_keeps_selection_scope_and_replaces_only_selection(self):
        text = "潮" * 13 + "\n\t"
        parent = self.enqueue(
            text, task="polish", instruction="润色正文，保持原文的80%至120%。",
            response_format="json",
        )
        original = self.process(parent)
        self.assertEqual(moyu_revision.candidate_text(original["result"]), text)
        self.assertFalse(original["result"]["output_validation"]["eligible"])
        parent_transport = original["result"]["text"]
        contract = self.frozen_contract(parent)
        status, created = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise",
            self.revision_request(parent, text),
        )
        self.assertEqual(status, 202, created)
        child = created["job"]
        self.assertEqual(child["task"], "polish")
        self.assertEqual(child["source"], parent["source"])
        self.assertEqual(self.frozen_contract(child), contract)
        with LocalProseProvider.lock:
            LocalProseProvider.text = UNICODE_PROSE
        completed = self.process(child)
        self.assertEqual(completed["result"]["structured"]["content"], UNICODE_PROSE)
        with LocalProseProvider.lock:
            material = json.loads(LocalProseProvider.calls[-1]["messages"][-1]["content"])
        self.assertEqual(material["candidate_text"], text)
        self.assertNotEqual(material["candidate_text"], parent_transport)
        status, applied = self.request(
            "POST", f"/api/ai/runs/{child['ai_run_id']}/apply",
            self.application(child, UNICODE_PROSE),
        )
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])
        selected = parent["source"]["range"]
        current = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.assertEqual(current["body"], BODY[:selected["start"]] + UNICODE_PROSE + BODY[selected["end"]:])
        status, stored = self.request("GET", "/api/ai/jobs/" + parent["id"])
        self.assertEqual(status, 200, stored)
        self.assertEqual(stored["result"]["text"], parent_transport)
        self.assertEqual(stored["result"]["structured"]["content"], text)

    def test_revision_rejects_manually_edited_parent_before_any_new_job_or_call(self):
        text = "潮" * 18
        parent = self.enqueue(text)
        self.process(parent)
        rejected = self.assert_revision_rejected(
            parent, self.revision_request(parent, text + "改"),
        )
        self.assertIn("候选已被编辑", rejected["error"])

    def test_revision_rejects_already_adopted_parent_before_any_new_job_or_call(self):
        text = "雨" * 12
        parent = self.enqueue(text)
        self.process(parent)
        status, applied = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/apply",
            self.application(parent, text),
        )
        self.assertEqual(status, 200, applied)
        rejected = self.assert_revision_rejected(parent, self.revision_request(parent, text))
        self.assertIn("已有采纳或拒绝记录", rejected["error"])

    def test_revision_rejects_old_manuscript_before_any_new_job_or_call(self):
        text = "潮" * 18
        parent = self.enqueue(text)
        self.process(parent)
        status, changed = self.request("PATCH", "/api/chapters/" + self.chapter_id, {
            "body": BODY + "\n作者后来补了一句。",
        })
        self.assertEqual(status, 200, changed)
        self.assert_revision_rejected(
            parent, self.revision_request(parent, text), expected_status=409,
        )

    def test_revision_rejects_unknown_provider_execution_without_resending(self):
        text = "潮" * 18
        parent = self.enqueue(text)
        with LocalProseProvider.lock:
            LocalProseProvider.incomplete = True
        status, incomplete = self.request(
            "POST", f"/api/ai/jobs/{parent['id']}/process", {},
        )
        self.assertEqual(status, 200, incomplete)
        self.assertEqual(incomplete["status"], "failed")
        self.assertEqual(incomplete["result"]["completion_state"], "unknown")
        self.assertFalse(incomplete["result"]["safe_to_apply"])
        with server.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM ai_run WHERE id=?",
                (parent["ai_run_id"],)).fetchone()[0], "unknown")
        rejected = self.assert_revision_rejected(parent, self.revision_request(parent, text))
        self.assertIn("已完整结束", rejected["error"])


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ProseOutputHTTPTests))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
