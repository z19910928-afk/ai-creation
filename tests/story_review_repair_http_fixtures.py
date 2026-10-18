#!/usr/bin/env python3
"""Actual HTTP review repairs with an isolated DB and free local provider."""
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


PAST_QUOTE = "林岚把唯一的钥匙交给了守灯人。"
CURRENT_QUOTE = "林岚把钥匙握在手中。"
REPEATED_QUOTE = "林岚抬头。"
CURRENT_BODY = "😀潮声停了。\n" + CURRENT_QUOTE + "\n" + REPEATED_QUOTE + "\n风从门缝吹进来。\n" + REPEATED_QUOTE + "\n她听见北塔的钟声。"
REPLACEMENT = "林岚攥紧空拳，指节抵着掌心。"


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class LocalProvider(BaseHTTPRequestHandler):
    calls = []
    lock = threading.Lock()

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with self.lock:
            self.calls.append(payload)
        messages = payload["messages"]
        user = messages[-1]["content"]
        context = json.loads(user.split("<manuscript>\n", 1)[1].split("\n</manuscript>", 1)[0])
        current = context["current_chapter"]
        if "人物动机、因果、时间" in messages[0]["content"]:
            sent_chapters = context.get("previous_chapters", []) + context.get("retrieved_chapters", [])
            previous = next(item for item in sent_chapters if PAST_QUOTE in item["excerpt"])
            second_start = CURRENT_BODY.rindex(REPEATED_QUOTE)
            output = json.dumps({
                "findings": [
                    {
                        "category": "continuity", "severity": "contradiction",
                        "message": "钥匙已交出，后章仍写握在手中。",
                        "suggestion": "用空拳保留她紧张的动作。",
                        "evidence": [
                            {"source_chapter_id": previous["id"], "source_quote": PAST_QUOTE},
                            {"source_chapter_id": current["id"], "source_quote": CURRENT_QUOTE},
                        ],
                    },
                    {
                        "category": "style", "severity": "suggestion",
                        "message": "第二处抬头可以换成具体动作。",
                        "suggestion": "保留听见钟声的后续动作。",
                        "evidence": [{
                            "source_chapter_id": current["id"], "source_quote": REPEATED_QUOTE,
                            "source_start": second_start, "source_end": second_start + len(REPEATED_QUOTE),
                        }],
                    },
                ],
                "uncertainties": ["固定本机模型仅验证修订流程，不衡量文学质量。"],
            }, ensure_ascii=False)
        else:
            output = REPLACEMENT
        body = encode({
            "model": "local-review-repair-fixture",
            "choices": [{"message": {"content": output}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 25, "total_tokens": 105},
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class StoryReviewRepairHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db = server.DB_PATH
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), LocalProvider)
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
        self.directory = tempfile.TemporaryDirectory(prefix="moyu-story-review-repair-")
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
        with LocalProvider.lock:
            LocalProvider.calls.clear()

        class AppHandler(server.Handler):
            def log_message(self, *_args):
                return

        self.app = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        self.app.daemon_threads = True
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_app)
        self.base = f"http://127.0.0.1:{self.app.server_port}"
        status, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200, state)
        self.past = state["chapters"][0]["id"]
        status, changed = self.request("PATCH", "/api/chapters/" + self.past, {
            "title": "第一章 交出钥匙", "body": PAST_QUOTE + "\n守灯人带着钥匙走向北塔。",
            "pov": "全知视角", "chapter_goal": "交出钥匙", "scene_plan": "交出钥匙后离开渡口",
        })
        self.assertEqual(status, 200, changed)
        status, created = self.request("POST", "/api/chapters", {
            "title": "第二章 北塔钟声", "body": CURRENT_BODY, "pov": "全知视角",
            "chapter_goal": "确认钥匙去向", "scene_plan": "守灯人带着钥匙离开，林岚仍在渡口",
        })
        self.assertEqual(status, 201, created)
        self.chapter_id = created["id"]
        status, settings = self.request("PUT", "/api/ai/settings", {
            "base_url": self.provider_base, "model": "local-review-repair-fixture",
            "api_key": "", "daily_limit": 10, "parameters": {"temperature": 0, "max_tokens": 3000},
        })
        self.assertEqual(status, 200, settings)
        status, job = self.request("POST", "/api/ai/queue", {
            "task": "semantic_review", "chapter_id": self.chapter_id, "defer": True,
            "response_format": "json",
            "material_controls": {"chapters": {
                self.past: {"pin": True, "range": {"start": 0, "end": len(PAST_QUOTE)}},
            }},
        })
        self.assertEqual(status, 202, job)
        status, completed = self.request("POST", f"/api/ai/jobs/{job['id']}/process", {})
        self.assertEqual((status, completed["status"]), (200, "success"), completed)
        status, reviews = self.request("GET", "/api/story-reviews?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, reviews)
        self.report = reviews["reviews"][0]
        self.assertEqual(len(self.report["findings"]), 2)
        self.assertEqual(len(self.report["findings"][0]["repair_targets"]), 2)
        self.assertEqual(len(self.report["findings"][1]["repair_targets"]), 1)
        self.assertEqual(self.call_count(), 1)

    def close_app(self):
        self.app.shutdown()
        self.app.server_close()
        self.thread.join(timeout=3)

    def request(self, method, route, payload=None, *, project=None, branch=None):
        request = urllib.request.Request(
            self.base + route, method=method,
            data=encode(payload) if payload is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Moyu-Project": project or self.project,
                "X-Moyu-Branch": branch or self.branch,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def call_count(self):
        with LocalProvider.lock:
            return len(LocalProvider.calls)

    def state(self):
        status, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200, state)
        return state

    def queue_snapshot(self):
        with server.connect() as conn:
            counts = tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                           for table in ("app_ai_job", "ai_run", "app_quota_reservation"))
            reservations = tuple(tuple(row) for row in conn.execute(
                "SELECT ai_run_id,status FROM app_quota_reservation ORDER BY rowid"))
        return counts, reservations, self.call_count()

    def repair_payload(self, issue_index=0, evidence_index=1):
        issue = self.report["findings"][issue_index]
        target = next(target for target in issue["repair_targets"] if target["evidence_index"] == evidence_index)
        return {
            "task": "polish", "chapter_id": target["source"]["chapter_id"],
            "source": copy.deepcopy(target["source"]),
            "review_source": {
                "review_id": self.report["id"], "issue_id": issue["id"], "evidence_index": evidence_index,
            },
            "instruction": "审稿问题：" + issue["message"] + "\n修订建议：" + issue["suggestion"],
            "response_format": "text", "defer": True,
        }

    def enqueue_repair(self, issue_index=0, evidence_index=1):
        payload = self.repair_payload(issue_index, evidence_index)
        status, job = self.request("POST", "/api/ai/queue", payload)
        self.assertEqual(status, 202, job)
        self.assertEqual(job["source"], payload["source"])
        return job

    def process(self, job):
        status, completed = self.request("POST", f"/api/ai/jobs/{job['id']}/process", {})
        self.assertEqual(status, 200, completed)
        return completed

    def apply_payload(self, job, text=REPLACEMENT):
        return {
            **copy.deepcopy(job["source"]), "replacement": text, "decision": "accepted",
            "candidate_hash": server.hash_text(text), "expected_head": self.state()["head_revision"],
            "reason": "作者已查看差异，确认修订这一处原句",
        }

    def test_selected_multi_evidence_repair_applies_exactly_and_is_idempotent(self):
        job = self.enqueue_repair()
        with server.connect() as conn:
            context = json.loads(conn.execute(
                "SELECT context_json FROM app_ai_job WHERE id=?", (job["id"],)).fetchone()[0])
        self.assertEqual(context["_review_source"], self.repair_payload()["review_source"])
        self.assertEqual(context["_source"]["range"]["quote"], CURRENT_QUOTE)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        with LocalProvider.lock:
            request = LocalProvider.calls[-1]
        user = request["messages"][-1]["content"]
        self.assertIn("钥匙已交出", user)
        self.assertIn("用空拳", user)
        material = json.loads(user.split("<manuscript>\n", 1)[1].split("\n</manuscript>", 1)[0])
        self.assertEqual(material["current_chapter"]["body"], CURRENT_QUOTE)
        self.assertEqual(material["current_chapter"]["source_range"], {
            "start": CURRENT_BODY.index(CURRENT_QUOTE), "end": CURRENT_BODY.index(CURRENT_QUOTE) + len(CURRENT_QUOTE),
        })
        before = self.state()
        payload = self.apply_payload(job)
        status, applied = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/apply", payload)
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])
        after = self.state()
        selected = job["source"]["range"]
        expected = CURRENT_BODY[:selected["start"]] + REPLACEMENT + CURRENT_BODY[selected["end"]:]
        self.assertEqual(next(chapter for chapter in after["chapters"] if chapter["id"] == self.chapter_id)["body"], expected)
        self.assertEqual(next(chapter for chapter in after["chapters"] if chapter["id"] == self.past)["body"],
                         next(chapter for chapter in before["chapters"] if chapter["id"] == self.past)["body"])
        status, repeated = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/apply", payload)
        self.assertEqual(status, 200, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.state()["chapters"], after["chapters"])
        status, reports = self.request("GET", "/api/story-reviews?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, reports)
        self.assertTrue(reports["reviews"][0]["stale"])
        self.assertEqual(reports["reviews"][0]["findings"][0]["repair_targets"], [])

    def test_exact_second_duplicate_with_emoji_prefix_uses_the_saved_range(self):
        payload = self.repair_payload(1, 0)
        selected = payload["source"]["range"]
        self.assertEqual(selected["start"], CURRENT_BODY.rindex(REPEATED_QUOTE))
        self.assertGreater(selected["start"], CURRENT_BODY.index(REPEATED_QUOTE))
        job = self.enqueue_repair(1, 0)
        self.assertEqual(self.process(job)["status"], "success")
        status, applied = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/apply", self.apply_payload(job))
        self.assertEqual(status, 200, applied)
        current = next(chapter for chapter in self.state()["chapters"] if chapter["id"] == self.chapter_id)
        self.assertEqual(current["body"], CURRENT_BODY[:selected["start"]] + REPLACEMENT + CURRENT_BODY[selected["end"]:])
        self.assertIn(REPEATED_QUOTE, current["body"][:selected["start"]])
        self.assertTrue(current["body"].startswith("😀潮声停了。"))

    def test_wrong_range_binding_and_malformed_sources_do_not_reserve_or_dispatch(self):
        base = self.repair_payload(1, 0)
        wrong_range = copy.deepcopy(base)
        first = CURRENT_BODY.index(REPEATED_QUOTE)
        wrong_range["source"]["range"] = {"start": first, "end": first + len(REPEATED_QUOTE), "quote": REPEATED_QUOTE}
        wrong_issue = copy.deepcopy(base)
        wrong_issue["review_source"] = self.repair_payload()["review_source"]
        missing_source = copy.deepcopy(base)
        del missing_source["source"]
        wrong_task = copy.deepcopy(base)
        wrong_task["task"] = "continue"
        cases = [(wrong_range, 409), (wrong_issue, 409), (missing_source, 409), (wrong_task, 400)]
        for value in (None, True, -1, "0", 1.5, 99):
            invalid = copy.deepcopy(base)
            invalid["review_source"]["evidence_index"] = value
            cases.append((invalid, 400))
        snapshot = self.queue_snapshot()
        for payload, expected in cases:
            with self.subTest(payload=payload["review_source"], expected=expected):
                status, rejected = self.request("POST", "/api/ai/queue", payload)
                self.assertEqual(status, expected, rejected)
                self.assertEqual(self.queue_snapshot(), snapshot)

    def test_old_report_submission_does_not_create_job_or_reservation(self):
        payload = self.repair_payload()
        status, changed = self.request("PATCH", "/api/chapters/" + self.past, {
            "body": "林岚保留了唯一的钥匙，守灯人独自离开。",
        })
        self.assertEqual(status, 200, changed)
        snapshot = self.queue_snapshot()
        status, rejected = self.request("POST", "/api/ai/queue", payload)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(rejected.get("code"), "review_source_stale")
        self.assertEqual(self.queue_snapshot(), snapshot)

    def test_restored_identical_body_does_not_reactivate_an_old_review(self):
        payload = self.repair_payload()
        status, changed = self.request("PATCH", "/api/chapters/" + self.chapter_id, {
            "body": CURRENT_BODY + "\n作者加入一行新稿。",
        })
        self.assertEqual(status, 200, changed)
        status, restored = self.request("POST", "/api/chapters/" + self.chapter_id + "/restore-revision", {
            "revision_id": self.report["source_revision_id"],
            "expected_head": self.state()["head_revision"],
        })
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored["body"], CURRENT_BODY)
        self.assertNotEqual(restored["revision_id"], self.report["source_revision_id"])
        self.assertEqual(server.hash_text(restored["body"]), payload["source"]["source_body_hash"])
        snapshot = self.queue_snapshot()
        status, rejected = self.request("POST", "/api/ai/queue", payload)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(rejected.get("code"), "review_source_stale")
        self.assertEqual(self.queue_snapshot(), snapshot)
        status, reports = self.request("GET", "/api/story-reviews?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, reports)
        self.assertTrue(reports["reviews"][0]["stale"])
        self.assertEqual(reports["reviews"][0]["findings"][0]["repair_targets"], [])

    def test_queued_repair_rechecks_report_before_provider_dispatch(self):
        job = self.enqueue_repair()
        calls_before = self.call_count()
        status, changed = self.request("PATCH", "/api/chapters/" + self.past, {
            "body": "林岚没有交出钥匙，守灯人拿走的是铜环。",
        })
        self.assertEqual(status, 200, changed)
        completed = self.process(job)
        self.assertEqual(completed["status"], "failed", completed)
        self.assertEqual(completed["result"]["billing_state"], "not_dispatched")
        self.assertFalse(completed["result"]["safe_to_apply"])
        self.assertEqual(self.call_count(), calls_before)
        with server.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation WHERE ai_run_id=?", (job["ai_run_id"],)).fetchone()[0], "released")
        status, quota = self.request("GET", "/api/ai/quota")
        self.assertEqual(status, 200, quota)
        self.assertEqual(quota["used_runs"], 1)
        self.assertEqual(quota["reserved_runs"], 0)

    def test_other_chapter_edit_after_generation_rejects_application(self):
        job = self.enqueue_repair()
        self.assertEqual(self.process(job)["status"], "success")
        status, changed = self.request("PATCH", "/api/chapters/" + self.past, {
            "body": "林岚没有交出钥匙，守灯人拿走的是铜环。",
        })
        self.assertEqual(status, 200, changed)
        before = self.state()
        snapshot = self.queue_snapshot()
        status, rejected = self.request("POST", f"/api/ai/runs/{job['ai_run_id']}/apply", self.apply_payload(job))
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.state()["chapters"], before["chapters"])
        self.assertEqual(self.queue_snapshot(), snapshot)
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_candidate_application").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT author_decision FROM ai_run WHERE id=?", (job["ai_run_id"],)).fetchone()[0], "pending")


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(StoryReviewRepairHTTPTests))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
