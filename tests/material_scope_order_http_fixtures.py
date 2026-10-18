#!/usr/bin/env python3
"""Frozen material sources include effective chapter order and plan titles."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from guidance_alias_http_fixtures import GuidanceAliasHTTP, Provider, encode
from guidance_scope_change_http_fixtures import EvidenceResult
from material_dependency_http_fixtures import MaterialDependencyHTTP
from backend import server


PROJECT = "default-project"
ORDER_CANARY = "ACTIVE_CHAPTER_INDEX_SCOPE_CANARY"
PLAN_CANARY = "FORESHADOW_PLAN_SOURCE_TITLE_CANARY"
PROFILE = {"chapter_top_k": 0, "recent_window": 0, "memory_top_k": 0, "record_top_k": 10}
SOURCES = (
    "backend/moyu_materials.py", "backend/server.py", "backend/moyu_analysis.py", "backend/moyu_context.py",
    "backend/moyu_workspace.py", "backend/moyu_records.py", "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py",
    "tests/material_dependency_http_fixtures.py",
    "tests/material_scope_order_http_fixtures.py",
)


class PlanningProvider(Provider):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Provider.requests.append(payload)
        Provider.total_calls += 1
        text = "雨水顺着窗框淌下，她松开抵着铜扣的手。"
        if payload.get("response_format", {}).get("type") == "json_object":
            text = json.dumps({
                "goal": "核对当前规划来源", "conflict": "合成规划中的等待",
                "turn": "重新确认章节位置", "scenes": ["北窗等候"], "foreshadowing": [],
            }, ensure_ascii=False)
        raw = encode({
            "model": "guidance-alias-fixture",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110},
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class MaterialScopeOrderHTTP(unittest.TestCase):
    observations = []
    queue = GuidanceAliasHTTP.queue
    process = GuidanceAliasHTTP.process
    preview = GuidanceAliasHTTP.preview
    body = GuidanceAliasHTTP.body
    state = MaterialDependencyHTTP.state
    ledger = MaterialDependencyHTTP.ledger
    change = MaterialDependencyHTTP.change
    frozen = MaterialDependencyHTTP.frozen
    apply = MaterialDependencyHTTP.apply

    def setUp(self):
        self.exchanges = []
        GuidanceAliasHTTP.setUp(self)
        self.provider.RequestHandlerClass = PlanningProvider
        self.request["retrieval_profile"] = dict(PROFILE)

    def tearDown(self):
        capture = os.environ.get("MOYU_MATERIAL_SCOPE_ORDER_CAPTURE")
        if capture:
            target = Path(capture) / self._testMethodName
            target.mkdir(parents=True, exist_ok=False)
            with server.connect() as source, sqlite3.connect(target / "synthetic-book.sqlite3") as dest:
                source.backup(dest)
            (target / "http-and-provider.json").write_text(json.dumps({
                "synthetic_only": True, "paid_provider_calls": 0,
                "http": self.exchanges, "provider_requests": Provider.requests,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        GuidanceAliasHTTP.tearDown(self)

    def call(self, method, route, payload=None, project=PROJECT):
        status, response = GuidanceAliasHTTP.call(self, method, route, payload, project)
        self.exchanges.append({
            "method": method, "route": route, "project": project,
            "request": copy.deepcopy(payload), "status": status, "response": copy.deepcopy(response),
        })
        return status, response

    def observe(self, scenario, **values):
        self.observations.append({"test": self._testMethodName, "scenario": scenario, **values})

    def source_card_at_current_index(self):
        status, chapter = self.call("POST", "/api/chapters", {
            "title": "章序校验当前章", "body": "林岚看向北窗，继续等待。", "pov": "林岚",
        })
        self.assertEqual(status, 201, chapter)
        self.chapter_id = chapter["id"]
        self.request["chapter_id"] = self.chapter_id
        chapters = self.state()["chapters"]
        number = next(index + 1 for index, item in enumerate(chapters) if item["id"] == self.chapter_id)
        self.assertGreaterEqual(number, 3)
        status, card = self.call("POST", "/api/records", {
            "type": "设定", "name": "当前章序可见设定", "description": ORDER_CANARY,
            "available_from": number,
        })
        self.assertEqual(status, 201, card)
        self.assertIn(ORDER_CANARY, self.preview()["user"])
        return chapters[0], card, number

    def delete_unconsumed_early_chapter(self, chapter, number):
        status, result = self.call("DELETE", "/api/chapters/" + chapter["id"], {
            "reason": "删除独立合成早章以验证实际章序变化。",
        })
        self.assertEqual(status, 200, result)
        current_number = next(index + 1 for index, item in enumerate(self.state()["chapters"])
                              if item["id"] == self.chapter_id)
        self.assertEqual(current_number, number - 1)
        self.assertNotIn(ORDER_CANARY, self.preview()["user"])
        return current_number

    def foreshadow_plan(self):
        source = self.state()["chapters"][-1]
        status, chapter = self.call("POST", "/api/chapters", {
            "title": "伏笔规划当前章", "body": "林岚握着铜扣，继续等候。", "pov": "林岚",
        })
        self.assertEqual(status, 201, chapter)
        self.chapter_id = chapter["id"]
        self.request["chapter_id"] = self.chapter_id
        status, plot = self.call("POST", "/api/plot", {"text": "合成铜扣规划"})
        self.assertEqual(status, 201, plot)
        status, link = self.call("POST", "/api/foreshadow-links", {
            "plot_id": plot["id"], "chapter_id": source["id"], "kind": "plant", "note": PLAN_CANARY,
        })
        self.assertEqual(status, 201, link)
        self.assertEqual(link["evidence"], {})
        self.request.update(task="outline", instruction="按当前伏笔规划拟定大纲。")
        preview = self.preview()
        self.assertIn(PLAN_CANARY, preview["user"])
        self.assertIn(source["title"], preview["user"])
        return source, link

    def assert_dispatch_blocked(self, job, scenario, **details):
        frozen, consent = self.frozen(job)
        calls, body = len(Provider.requests), self.body()
        result = self.process(job)
        context, current_consent = self.frozen(job)
        self.observe(
            scenario, dispatch_status=result["status"],
            billing_state=result.get("result", {}).get("billing_state"),
            extra_provider_calls=len(Provider.requests) - calls,
            body_unchanged=self.body() == body, frozen_context_unchanged=context == frozen,
            request_hash_preserved=(current_consent["request_audit"]["messages_sha256"]
                                    == consent["request_audit"]["messages_sha256"]),
            material_read_only=result.get("materials_read_only"), **details,
        )
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["result"]["billing_state"], "not_dispatched", result)
        self.assertEqual(len(Provider.requests), calls)
        self.assertEqual(self.body(), body)
        self.assertEqual(context, frozen)
        self.assertEqual(current_consent["request_audit"]["messages_sha256"],
                         consent["request_audit"]["messages_sha256"])

    def assert_original_retries_blocked(self, job, scenario):
        ledger, body, calls = self.ledger(), self.body(), len(Provider.requests)
        outcomes = []
        with patch("backend.server.start_ai_worker", return_value=False):
            for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, result = self.call("POST", f"/api/ai/{kind}/{identifier}/retry", {"mode": "original"})
                outcomes.append({"entry": kind, "status": status})
        self.observe(scenario, outcomes=outcomes, ledger_unchanged=self.ledger() == ledger,
                     body_unchanged=self.body() == body, extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual([item["status"] for item in outcomes], [409, 409], result)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), calls)

    def test_deleted_early_chapter_blocks_frozen_dispatch(self):
        early, _, number = self.source_card_at_current_index()
        job = self.queue()
        context, _ = self.frozen(job)
        marker = context["_material_source"]
        self.assertFalse(any(item["kind"] == "chapter" and item["id"] == early["id"]
                             for item in marker["dependencies"]))
        current_number = self.delete_unconsumed_early_chapter(early, number)
        self.assert_dispatch_blocked(job, "deleted_early_chapter",
                                     original_chapter_number=number, current_chapter_number=current_number)

    def test_deleted_early_chapter_blocks_completed_candidate_apply(self):
        early, _, number = self.source_card_at_current_index()
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.assertIn(ORDER_CANARY, json.dumps(Provider.requests, ensure_ascii=False))
        self.delete_unconsumed_early_chapter(early, number)
        ledger, body, calls = self.ledger(), self.body(), len(Provider.requests)
        status, result = self.apply(job, completed)
        self.observe("deleted_early_chapter_old_apply", http_status=status, applied=result.get("applied"),
                     ledger_unchanged=self.ledger() == ledger, body_unchanged=self.body() == body,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), calls)

    def test_deleted_early_chapter_blocks_original_retries(self):
        early, _, number = self.source_card_at_current_index()
        job = self.queue()
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.delete_unconsumed_early_chapter(early, number)
        self.assert_original_retries_blocked(job, "deleted_early_chapter_original_retry")

    def test_foreshadow_plan_title_change_blocks_frozen_dispatch(self):
        source, link = self.foreshadow_plan()
        job = self.queue()
        context, _ = self.frozen(job)
        self.assertTrue(any(item["id"] == link["id"] for item in context["author_plans"]))
        self.assertFalse(any(item["kind"] == "chapter" and item["id"] == source["id"]
                             for item in context["_material_source"]["dependencies"]))
        changed_title = "伏笔来源章节的当前新标题"
        self.change("/api/chapters/" + source["id"], title=changed_title)
        self.assertIn(changed_title, self.preview()["user"])
        self.assert_dispatch_blocked(job, "consumed_foreshadow_plan_title",
                                     original_title=source["title"], current_title=changed_title)

    def test_foreshadow_plan_title_change_blocks_original_retries(self):
        source, _ = self.foreshadow_plan()
        job = self.queue()
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.change("/api/chapters/" + source["id"], title="取消后伏笔来源新标题")
        self.assert_original_retries_blocked(job, "foreshadow_plan_title_original_retry")

    def test_unconsumed_early_chapter_title_does_not_expire_task(self):
        early, _, _ = self.source_card_at_current_index()
        job = self.queue()
        self.change("/api/chapters/" + early["id"], title="未消费早章的当前新标题")
        completed = self.process(job)
        sent = json.dumps(Provider.requests, ensure_ascii=False)
        self.observe("unconsumed_early_chapter_title", dispatch_status=completed["status"],
                     free_provider_calls=len(Provider.requests), source_title_absent="未消费早章的当前新标题" not in sent)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertNotIn("未消费早章的当前新标题", sent)

    def test_omitted_foreshadow_title_does_not_expire_planning_task(self):
        source, link = self.foreshadow_plan()
        self.request["material_controls"] = {"plans": {link["id"]: {"omit": True}}}
        self.assertNotIn(PLAN_CANARY, self.preview()["user"])
        job = self.queue()
        self.change("/api/chapters/" + source["id"], title="未消费伏笔来源新标题")
        completed = self.process(job)
        sent = json.dumps(Provider.requests, ensure_ascii=False)
        self.observe("omitted_foreshadow_title", dispatch_status=completed["status"],
                     free_provider_calls=len(Provider.requests), omitted_plan_absent=PLAN_CANARY not in sent)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertNotIn(PLAN_CANARY, sent)

    def test_current_scope_after_early_deletion_can_generate_and_apply(self):
        early, _, number = self.source_card_at_current_index()
        self.delete_unconsumed_early_chapter(early, number)
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        body = self.body()
        status, result = self.apply(job, completed)
        self.observe("current_chapter_order_control", dispatch_status=completed["status"],
                     apply_http_status=status, body_changed=self.body() != body,
                     future_card_absent=ORDER_CANARY not in json.dumps(Provider.requests, ensure_ascii=False))
        self.assertEqual(status, 200, result)
        self.assertTrue(result["applied"])
        self.assertNotEqual(self.body(), body)
        self.assertNotIn(ORDER_CANARY, json.dumps(Provider.requests, ensure_ascii=False))

    def test_current_foreshadow_title_can_generate_new_plan(self):
        source, _ = self.foreshadow_plan()
        changed_title = "规划生成前保存的来源标题"
        self.change("/api/chapters/" + source["id"], title=changed_title)
        job = self.queue()
        completed = self.process(job)
        sent = json.dumps(Provider.requests, ensure_ascii=False)
        self.observe("current_foreshadow_title_control", dispatch_status=completed["status"],
                     current_title_sent=changed_title in sent, free_provider_calls=len(Provider.requests))
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertIn(changed_title, sent)
        self.assertIn(PLAN_CANARY, sent)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def main():
    output = os.environ.get("MOYU_MATERIAL_SCOPE_ORDER_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    Provider.total_calls = 0
    MaterialScopeOrderHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MaterialScopeOrderHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(MaterialScopeOrderHTTP.observations),
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "scope": "temporary synthetic SQLite; real loopback HTTP; free local provider; no real manuscript or credential",
        "claim_limit": "chapter-scope and source-title integrity only; no literary-quality or competitiveness claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
