#!/usr/bin/env python3
"""Frozen requests remain bound to the exact materials they consumed."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from guidance_alias_http_fixtures import GuidanceAliasHTTP, Provider
from guidance_scope_change_http_fixtures import EvidenceResult
import moyu_analysis
import moyu_records
import moyu_workspace
import server


PROJECT = "default-project"
CANARY = "MATERIAL_DEPENDENCY_CANARY"
SOURCES = (
    "server.py", "moyu_analysis.py", "moyu_context.py", "moyu_workspace.py",
    "moyu_records.py", "moyu_materials.py", "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py",
    "tests/material_dependency_http_fixtures.py",
)


class MaterialDependencyHTTP(unittest.TestCase):
    observations = []
    queue = GuidanceAliasHTTP.queue
    process = GuidanceAliasHTTP.process
    preview = GuidanceAliasHTTP.preview
    body = GuidanceAliasHTTP.body

    def setUp(self):
        GuidanceAliasHTTP.setUp(self)

    def tearDown(self):
        GuidanceAliasHTTP.tearDown(self)

    def call(self, method, route, payload=None, project=PROJECT):
        return GuidanceAliasHTTP.call(self, method, route, payload, project)

    def observe(self, scenario, **values):
        self.observations.append({"test": self._testMethodName, "scenario": scenario, **values})

    def state(self):
        status, state = self.call("GET", "/api/state")
        self.assertEqual(status, 200, state)
        return state

    def ledger(self):
        with server.connect() as conn:
            snapshots = {}
            for table in ("app_ai_job", "ai_run", "app_quota_reservation",
                          "app_candidate_application", "app_candidate_revision",
                          "app_chapter_analysis", "app_story_review", "app_chapter_meta",
                          "document_revision", "event_log"):
                rows = [
                    {key: {"blob_sha256": hashlib.sha256(value).hexdigest()}
                     if isinstance(value, bytes) else value for key, value in dict(row).items()}
                    for row in conn.execute("SELECT * FROM " + table + " ORDER BY rowid")
                ]
                snapshots[table] = {
                    "count": len(rows),
                    "rows_sha256": hashlib.sha256(json.dumps(
                        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ).encode("utf-8")).hexdigest(),
                }
            return snapshots

    def change(self, route, **fields):
        status, item = self.call("PATCH", route, fields)
        self.assertEqual(status, 200, item)
        return item

    def card(self):
        status, card = self.call("POST", "/api/records", {
            "type": "设定", "name": "本次使用的设定", "description": CANARY,
        })
        self.assertEqual(status, 201, card)
        return card

    def prior(self):
        chapters = self.state()["chapters"]
        index = next(i for i, chapter in enumerate(chapters) if chapter["id"] == self.chapter_id)
        quote = "林岚从北窗接过刻有雨纹的铜扣，来客随后沿石阶离去。"
        if index == 0:
            status, chapter = self.call("POST", "/api/chapters", {
                "title": "合成资料来源早章", "body": quote,
            })
            self.assertEqual(status, 201, chapter)
            # The public creator appends; reorder only this temporary fixture.
            with server.connect() as conn:
                conn.execute("UPDATE document_node SET ordinal=ordinal+1 WHERE project_id=? "
                             "AND branch_id=? AND id<>?", (PROJECT, "main", chapter["id"]))
                conn.execute("UPDATE document_node SET ordinal=1 WHERE id=?", (chapter["id"],))
        else:
            chapter = chapters[index - 1]
        self.change("/api/chapters/" + chapter["id"], body=quote)
        return {**chapter, "body": quote}

    def evidence(self, chapter):
        evidence = {
            "source_chapter_id": chapter["id"], "source_quote": chapter["body"],
            "source_start": 0, "source_end": len(chapter["body"]),
            "source_hash": server.hash_text(chapter["body"]), "epistemic_mode": "world_truth",
        }
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                span_id = server.source_span_for_memory(conn, evidence)
                self.assertTrue(span_id)
                evidence["source_span_id"] = span_id
                evidence["source_revision_id"] = conn.execute(
                    "SELECT revision_id FROM source_span WHERE id=?", (span_id,)).fetchone()[0]
        return evidence

    def memory(self):
        chapter = self.prior()
        status, memory = self.call("POST", "/api/memory", {
            "type": "人物", "name": self.person["name"], "note": CANARY, "status": "accepted",
            "source_chapter_id": chapter["id"], "source_quote": chapter["body"],
            "source_start": 0, "source_end": len(chapter["body"]),
        })
        self.assertEqual(status, 201, memory)
        self.assertIn(CANARY, self.preview()["user"])
        return memory

    def character_state(self):
        status, state = self.call("POST", "/api/character-states", {
            "entity_id": self.person["id"], "attribute": "持有物",
            "value_json": {"item": CANARY, "details": {"rain": True, "count": 1}},
            "knowledge_scope_json": {}, "valid_start": "1",
        })
        self.assertEqual(status, 201, state)
        self.assertIn(CANARY, self.preview()["user"])
        return state

    def relation(self):
        chapter = self.prior()
        status, other = self.call("POST", "/api/records", {
            "type": "人物", "name": "石阶来客", "description": "合成资料的另一个人物。",
        })
        self.assertEqual(status, 201, other)
        status, relation = self.call("POST", "/api/relationships", {
            "from_entity_id": self.person["id"], "to_entity_id": other["id"],
            "relation_type": "同行", "note": CANARY,
        })
        self.assertEqual(status, 201, relation)
        evidence = self.evidence(chapter)
        with server.connect() as conn:
            conn.execute("UPDATE app_story_relation SET evidence_json=? WHERE id=?",
                         (server.json_text(evidence), relation["id"]))
        self.assertIn(CANARY, self.preview()["user"])
        return relation, chapter, evidence

    def timeline(self):
        chapter = self.prior()
        status, event = self.call("POST", "/api/timeline", {
            "title": "铜扣交接", "chapter_id": chapter["id"],
            "entity_ids": [self.person["id"]], "summary": CANARY, "status": "occurred",
        })
        self.assertEqual(status, 201, event)
        evidence = self.evidence(chapter)
        with server.connect() as conn:
            conn.execute("UPDATE app_timeline_event SET evidence_json=? WHERE id=?",
                         (server.json_text(evidence), event["id"]))
        self.assertIn(CANARY, self.preview()["user"])
        return event, chapter, evidence

    def thread(self):
        status, thread = self.call("POST", "/api/plot-threads", {
            "status": "open", "payload_json": {"summary": CANARY, "item": "铜扣"},
            "severity": "medium",
        })
        self.assertEqual(status, 201, thread)
        self.assertIn(CANARY, self.preview()["user"])
        return thread

    def frozen(self, job):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT j.context_json,r.consent_json FROM app_ai_job j "
                "JOIN ai_run r ON r.id=j.ai_run_id WHERE j.id=?", (job["id"],)).fetchone()
        return json.loads(row["context_json"]), json.loads(row["consent_json"])

    def strip_marker(self, job):
        context, _ = self.frozen(job)
        context.pop("_material_source", None)
        with server.connect() as conn:
            conn.execute("UPDATE app_ai_job SET context_json=? WHERE id=?",
                         (server.json_text(context), job["id"]))

    def assert_dispatch_blocked(self, job, scenario):
        body, calls = self.body(), len(Provider.requests)
        before_context, before_consent = self.frozen(job)
        old_hash = before_consent["request_audit"]["messages_sha256"]
        result = self.process(job)
        context, consent = self.frozen(job)
        self.observe(scenario, dispatch_status=result["status"],
                     billing_state=result.get("result", {}).get("billing_state"),
                     extra_provider_calls=len(Provider.requests) - calls,
                     body_unchanged=self.body() == body,
                     frozen_context_unchanged=context == before_context,
                     messages_hash_preserved=consent["request_audit"]["messages_sha256"] == old_hash,
                     material_marker_present="_material_source" in before_context)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["result"]["billing_state"], "not_dispatched", result)
        self.assertEqual(len(Provider.requests), calls)
        self.assertEqual(self.body(), body)
        self.assertEqual(context, before_context)
        self.assertEqual(consent["request_audit"]["messages_sha256"], old_hash)

    def apply(self, job, completed):
        return self.call("POST", "/api/ai/runs/" + job["ai_run_id"] + "/apply", {
            **job["source"], "decision": "accepted",
            "replacement": "\n\n" + completed["result"]["text"],
        })

    def review(self, frozen=None):
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                context, chapter = server.ai_context(conn, self.chapter_id, task="semantic_review")
                raw = {
                    "findings": [{
                        "category": "continuity", "severity": "possible",
                        "message": "合成审稿，只验证资料依赖，不证明文学效果。",
                        "suggestion": "由作者核对。",
                        "evidence": [{"source_chapter_id": self.chapter_id, "source_start": 0,
                                      "source_end": 19, "source_quote": chapter["body"][:19]}],
                    }],
                    "uncertainties": ["本机合成数据，没有模型调用。"],
                }
                return moyu_analysis.save_review(conn, {
                    "project_id": PROJECT, "branch_id": "main",
                    "sent_context": context if frozen is None else frozen,
                }, chapter, None, raw)

    def gate(self):
        with server.connect() as conn:
            return moyu_analysis.review_publication_gate(conn, {
                "project_id": PROJECT, "branch_id": "main",
            }, self.chapter_id)

    def analysis(self, frozen=None):
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                context, chapter = server.ai_context(conn, self.chapter_id, task="chapter_analysis")
                evidence = [{"source_chapter_id": self.chapter_id, "source_start": 0,
                             "source_end": 19, "source_quote": chapter["body"][:19]}]
                structured = {
                    "summary": {"text": "她在北窗听雨。", "evidence": evidence},
                    "handoff": {"text": "继续等待窗外的来客。", "evidence": evidence},
                    "candidates": [],
                }
                return moyu_analysis.save_analysis(conn, {
                    "project_id": PROJECT, "branch_id": "main", "actor_id": "fixture-author",
                    "records": server.state_from_db(conn)["records"],
                    "sent_context": context if frozen is None else frozen,
                }, chapter, None, structured)

    def confirm_analysis(self, analysis, **fields):
        return self.call("POST", "/api/chapter-analysis/" + analysis["id"] + "/confirm", {
            "source_revision_id": analysis["source_revision_id"],
            "chapter_hash": analysis["chapter_hash"], "decisions": [], **fields,
        })

    def analysis_rows(self):
        status, response = self.call("GET", "/api/chapter-analysis?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, response)
        return response["analyses"]

    def test_delayed_chapter_analysis_save_rejected_atomically(self):
        card = self.card()
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                context, _ = server.ai_context(conn, self.chapter_id, task="chapter_analysis")
        self.change("/api/records/" + card["id"], description="章后结果返回前资料已改变。")
        with server.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM app_chapter_analysis").fetchone()[0]
        rejected = False
        try:
            self.analysis(context)
        except moyu_analysis.AnalysisConflict:
            rejected = True
        with server.connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM app_chapter_analysis").fetchone()[0]
        self.observe("late_analysis_save", rejected=rejected, analysis_count_unchanged=before == after,
                     free_provider_calls=len(Provider.requests))
        self.assertTrue(rejected)
        self.assertEqual(before, after)
        self.assertEqual(len(Provider.requests), 0)

    def test_material_change_blocks_saved_analysis_confirmation_without_writes(self):
        card = self.card()
        analysis = self.analysis()
        self.change("/api/records/" + card["id"], description="章后确认之前资料已改变。")
        prior_state, ledger = self.state(), self.ledger()
        stored = self.analysis_rows()[0]
        status, response = self.confirm_analysis(analysis, confirm_summary=True, confirm_handoff=True)
        self.observe("stale_analysis_confirm", http_status=status, analysis_status=stored["status"],
                     state_unchanged=self.state() == prior_state, ledger_unchanged=self.ledger() == ledger,
                     free_provider_calls=len(Provider.requests))
        self.assertEqual(stored["status"], "stale", stored)
        self.assertEqual(status, 409, response)
        self.assertEqual(self.state(), prior_state)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(len(Provider.requests), 0)

    def test_analysis_own_confirmations_remain_current_and_idempotent(self):
        self.card()
        analysis = self.analysis()
        outcomes = []
        for fields in ({"confirm_summary": True}, {"confirm_summary": True},
                       {"confirm_handoff": True}, {"confirm_handoff": True}):
            status, response = self.confirm_analysis(analysis, **fields)
            outcomes.append(status)
            self.assertEqual(status, 200, response)
        saved = self.analysis_rows()[0]
        chapter = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.observe("analysis_confirmation_control", outcomes=outcomes, analysis_status=saved["status"],
                     summary_saved=chapter["recap"] == analysis["summary"]["text"],
                     handoff_saved=chapter["next_brief"] == analysis["handoff"]["text"])
        self.assertEqual(saved["status"], "reviewed", saved)
        self.assertEqual(chapter["recap"], analysis["summary"]["text"])
        self.assertEqual(chapter["next_brief"], analysis["handoff"]["text"])
        self.assertEqual(len(Provider.requests), 0)

    def test_record_description_change_blocks_old_dispatch(self):
        card = self.card()
        self.assertIn(CANARY, self.preview()["user"])
        job = self.queue()
        self.change("/api/records/" + card["id"], description="设定已改为另一条内容。")
        self.assert_dispatch_blocked(job, "record_description")

    def test_memory_retraction_blocks_old_dispatch(self):
        memory = self.memory()
        job = self.queue()
        self.change("/api/memory/" + memory["id"], status="rejected", reason="作者撤回合成事实。")
        self.assertNotIn(CANARY, self.preview()["user"])
        self.assert_dispatch_blocked(job, "memory_retracted")

    def test_memory_same_id_note_change_blocks_old_dispatch(self):
        memory = self.memory()
        job = self.queue()
        self.change("/api/memory/" + memory["id"], note="同一记忆已改为另一条确认内容。")
        self.assert_dispatch_blocked(job, "memory_note")

    def test_character_state_value_change_blocks_old_dispatch(self):
        state = self.character_state()
        job = self.queue()
        self.change("/api/character-states/" + state["id"], value_json={"item": "新的铜扣"})
        self.assert_dispatch_blocked(job, "character_state_value")

    def test_character_state_scope_change_blocks_old_dispatch(self):
        state = self.character_state()
        job = self.queue()
        self.change("/api/character-states/" + state["id"], knowledge_scope_json={"available_from": 99})
        self.assertNotIn(CANARY, self.preview()["user"])
        self.assert_dispatch_blocked(job, "character_state_scope")

    def test_previous_chapter_change_blocks_old_dispatch(self):
        chapter = self.prior()
        self.request["material_controls"] = {"chapters": {chapter["id"]: {"pin": True}}}
        self.assertIn(chapter["body"], self.preview()["user"])
        job = self.queue()
        self.change("/api/chapters/" + chapter["id"], body="铜扣交接的原文已被作者重写。")
        self.assert_dispatch_blocked(job, "previous_chapter")

    def test_relationship_note_change_blocks_old_dispatch(self):
        relation, _, _ = self.relation()
        job = self.queue()
        self.change("/api/relationships/" + relation["id"], note="同行关系已改为另一个描述。")
        self.assert_dispatch_blocked(job, "relationship_note")

    def test_relationship_evidence_change_blocks_old_dispatch(self):
        relation, _, evidence = self.relation()
        job = self.queue()
        evidence["epistemic_mode"] = "author_plan"
        with server.connect() as conn:
            conn.execute("UPDATE app_story_relation SET evidence_json=? WHERE id=?",
                         (server.json_text(evidence), relation["id"]))
        self.assertNotIn(CANARY, self.preview()["user"])
        self.assert_dispatch_blocked(job, "relationship_evidence")

    def test_timeline_summary_change_blocks_old_dispatch(self):
        event, _, _ = self.timeline()
        job = self.queue()
        self.change("/api/timeline/" + event["id"], summary="时间线已改为另一项事件。")
        self.assert_dispatch_blocked(job, "timeline_summary")

    def test_timeline_evidence_change_blocks_old_dispatch(self):
        event, _, evidence = self.timeline()
        job = self.queue()
        evidence["epistemic_mode"] = "author_plan"
        with server.connect() as conn:
            conn.execute("UPDATE app_timeline_event SET evidence_json=? WHERE id=?",
                         (server.json_text(evidence), event["id"]))
        self.assertNotIn(CANARY, self.preview()["user"])
        self.assert_dispatch_blocked(job, "timeline_evidence")

    def test_plot_thread_payload_change_blocks_old_dispatch(self):
        thread = self.thread()
        job = self.queue()
        self.change("/api/plot-threads/" + thread["id"], payload_json={"summary": "新线索", "item": "银扣"})
        self.assert_dispatch_blocked(job, "plot_thread")

    def test_delayed_context_creation_rejected_without_ledger_changes(self):
        card = self.card()
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                context, chapter = server.ai_context(conn, self.chapter_id, task="continue")
        self.change("/api/records/" + card["id"], description="资料已在插入任务之前修改。")
        ledger, body = self.ledger(), self.body()
        with patch("server.ai_context", return_value=(context, chapter)):
            status, result = self.call("POST", "/api/ai/queue", {**self.request, "defer": True})
        self.observe("late_creation", http_status=status, ledger_unchanged=self.ledger() == ledger,
                     body_unchanged=self.body() == body, free_provider_calls=len(Provider.requests))
        self.assertEqual(status, 400, result)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), 0)

    def test_completed_candidate_apply_rejected_after_material_change(self):
        card = self.card()
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.change("/api/records/" + card["id"], description="候选完成后设定已改变。")
        ledger, body, calls = self.ledger(), self.body(), len(Provider.requests)
        status, result = self.apply(job, completed)
        self.observe("stale_apply", http_status=status, ledger_unchanged=self.ledger() == ledger,
                     body_unchanged=self.body() == body, extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), calls)

    def test_completed_candidate_revision_rejected_without_child_task(self):
        card = self.card()
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.change("/api/records/" + card["id"], description="修改意见提交前设定已改变。")
        ledger, body, calls = self.ledger(), self.body(), len(Provider.requests)
        status, result = self.call("POST", "/api/ai/runs/" + job["ai_run_id"] + "/revise", {
            "request_id": "material-revision", "candidate_hash": server.hash_text(completed["result"]["text"]),
            "notes": "按当前资料调整。", "defer": True,
        })
        self.observe("stale_revise", http_status=status, ledger_unchanged=self.ledger() == ledger,
                     body_unchanged=self.body() == body, extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), calls)

    def test_canceled_job_and_run_original_retries_rejected(self):
        card = self.card()
        job = self.queue()
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.change("/api/records/" + card["id"], description="取消后设定已改变。")
        ledger, body = self.ledger(), self.body()
        outcomes = []
        with patch("server.start_ai_worker", return_value=False):
            for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, result = self.call("POST", f"/api/ai/{kind}/{identifier}/retry", {"mode": "original"})
                outcomes.append({"entry": kind, "http_status": status})
        self.observe("stale_retry", outcomes=outcomes, ledger_unchanged=self.ledger() == ledger,
                     body_unchanged=self.body() == body, free_provider_calls=len(Provider.requests))
        self.assertEqual([row["http_status"] for row in outcomes], [409, 409], result)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), 0)

    def test_delayed_semantic_review_save_rejected_atomically(self):
        card = self.card()
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                context, _ = server.ai_context(conn, self.chapter_id, task="semantic_review")
        self.change("/api/records/" + card["id"], description="审稿返回前资料已改变。")
        with server.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM app_story_review").fetchone()[0]
        rejected = False
        try:
            self.review(context)
        except moyu_analysis.AnalysisConflict:
            rejected = True
        with server.connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM app_story_review").fetchone()[0]
        self.observe("late_semantic_save", rejected=rejected, review_count_unchanged=before == after,
                     free_provider_calls=len(Provider.requests))
        self.assertTrue(rejected)
        self.assertEqual(before, after)
        self.assertEqual(len(Provider.requests), 0)

    def test_material_change_expires_explained_review_gate(self):
        card = self.card()
        report = self.review()
        status, resolved = self.call("POST", "/api/story-reviews/" + report["id"] + "/issues/" +
                                    report["findings"][0]["id"] + "/decision", {
            "decision": "explained", "reason": "作者已核对合成资料。",
        })
        self.assertEqual(status, 200, resolved)
        self.assertEqual(self.gate()["issues"], [])
        self.change("/api/records/" + card["id"], description="作者解释之后资料已改变。")
        status, response = self.call("GET", "/api/story-reviews?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, response)
        review = next(item for item in response["reviews"] if item["id"] == report["id"])
        codes = [issue["code"] for issue in self.gate()["issues"]]
        self.observe("explained_semantic_stale", stale=review.get("stale", False), gate_codes=codes,
                     explanation_preserved=review["findings"][0]["decision"] == "explained")
        self.assertTrue(review.get("stale", False), review)
        self.assertIn("semantic_review_stale", codes)
        self.assertEqual(review["findings"][0]["decision"], "explained")
        self.assertEqual(len(Provider.requests), 0)

    def test_omitted_card_change_does_not_expire_consumed_materials(self):
        card = self.card()
        self.request["material_controls"] = {"records": {card["id"]: {"omit": True}}}
        self.assertNotIn(CANARY, self.preview()["user"])
        job = self.queue()
        self.change("/api/records/" + card["id"], description="从未发给模型的资料已改变。")
        completed = self.process(job)
        self.observe("omitted_control", dispatch_status=completed["status"], free_provider_calls=len(Provider.requests),
                     provider_excludes_card=CANARY not in json.dumps(Provider.requests, ensure_ascii=False))
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertNotIn(CANARY, json.dumps(Provider.requests, ensure_ascii=False))

    def test_equivalent_json_order_is_not_a_material_change(self):
        state = self.character_state()
        thread = self.thread()
        job = self.queue()
        with server.connect() as conn:
            conn.execute("UPDATE character_state SET value_json=? WHERE id=?", (
                json.dumps({"details": {"count": 1, "rain": True}, "item": CANARY}, indent=2), state["id"]))
            conn.execute("UPDATE plot_thread SET payload_json=? WHERE id=?", (
                json.dumps({"item": "铜扣", "summary": CANARY}, indent=2), thread["id"]))
        completed = self.process(job)
        self.observe("equivalent_json", dispatch_status=completed["status"], free_provider_calls=len(Provider.requests))
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)

    def test_missing_and_empty_scope_are_equivalent(self):
        card = self.card()
        with server.connect() as conn:
            conn.execute("DELETE FROM app_entity_scope WHERE entity_id=?", (card["id"],))
        job = self.queue()
        with server.connect() as conn:
            moyu_records.write_scope(conn, card["id"], {}, server.now())
        completed = self.process(job)
        self.observe("empty_scope_control", dispatch_status=completed["status"], free_provider_calls=len(Provider.requests))
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)

    def test_other_book_material_change_does_not_expire_this_task(self):
        self.card()
        job = self.queue()
        status, other = self.call("POST", "/api/projects", {"title": "隔离的另一部合成作品"})
        self.assertEqual(status, 201, other)
        pid = other["project"]["id"]
        status, card = self.call("POST", "/api/records", {
            "type": "设定", "name": "另一部作品设定", "description": CANARY,
        }, pid)
        self.assertEqual(status, 201, card)
        status, changed = self.call("PATCH", "/api/records/" + card["id"],
                                    {"description": "他书资料已改变。"}, pid)
        self.assertEqual(status, 200, changed)
        completed = self.process(job)
        self.observe("other_book_control", dispatch_status=completed["status"], free_provider_calls=len(Provider.requests))
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)

    def test_current_materials_can_generate_and_apply(self):
        card = self.card()
        self.change("/api/records/" + card["id"], description=CANARY + "_CURRENT")
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        body = self.body()
        status, applied = self.apply(job, completed)
        self.observe("current_generation_control", dispatch_status=completed["status"], apply_http_status=status,
                     body_changed=self.body() != body, free_provider_calls=len(Provider.requests))
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])
        self.assertNotEqual(self.body(), body)
        self.assertIn(CANARY + "_CURRENT", json.dumps(Provider.requests, ensure_ascii=False))

    def test_legacy_marker_missing_blocks_dispatch_but_allows_read(self):
        self.card()
        job = self.queue()
        self.strip_marker(job)
        status, history = self.call("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, history)
        self.assertEqual(history["id"], job["id"])
        self.assert_dispatch_blocked(job, "legacy_dispatch_read")

    def test_legacy_marker_missing_blocks_completed_apply_but_allows_read(self):
        self.card()
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.strip_marker(job)
        ledger, body = self.ledger(), self.body()
        status, history = self.call("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, history)
        self.assertEqual(history["result"]["text"], completed["result"]["text"])
        status, result = self.apply(job, completed)
        self.observe("legacy_apply_read", http_status=status, ledger_unchanged=self.ledger() == ledger,
                     body_unchanged=self.body() == body, result_readable=True)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), 1)

    def test_legacy_marker_missing_blocks_job_and_run_original_retries(self):
        self.card()
        job = self.queue()
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.strip_marker(job)
        ledger, body = self.ledger(), self.body()
        outcomes = []
        with patch("server.start_ai_worker", return_value=False):
            for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, result = self.call("POST", f"/api/ai/{kind}/{identifier}/retry", {"mode": "original"})
                outcomes.append({"entry": kind, "http_status": status})
        self.observe("legacy_retry", outcomes=outcomes, ledger_unchanged=self.ledger() == ledger,
                     body_unchanged=self.body() == body, free_provider_calls=len(Provider.requests))
        self.assertEqual([row["http_status"] for row in outcomes], [409, 409], result)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), 0)

    def test_private_material_marker_does_not_enter_prompt_or_provider_request(self):
        self.card()
        preview = self.preview()
        job = self.queue()
        context, _ = self.frozen(job)
        marker = context.get("_material_source")
        self.assertIsInstance(marker, dict)
        hashes = [item["sha256"] for item in marker["dependencies"]]
        completed = self.process(job)
        sent = json.dumps(Provider.requests, ensure_ascii=False)
        visible_preview = json.dumps(preview, ensure_ascii=False)
        self.observe("private_marker", marker_present=True, dispatch_status=completed["status"],
                     internal_marker_absent="_material_source" not in sent and "_material_source" not in visible_preview,
                     private_hashes_absent=all(value not in sent and value not in visible_preview for value in hashes))
        self.assertEqual(completed["status"], "success", completed)
        self.assertNotIn("_material_source", sent)
        self.assertNotIn("_material_source", visible_preview)
        for value in hashes:
            self.assertNotIn(value, sent)
            self.assertNotIn(value, visible_preview)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() if (ROOT / name).exists() else None
            for name in SOURCES}


def main():
    output = os.environ.get("MOYU_MATERIAL_DEPENDENCY_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    integrated = "_material_source" in (ROOT / "server.py").read_text(encoding="utf-8")
    Provider.total_calls = 0
    MaterialDependencyHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MaterialDependencyHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(MaterialDependencyHTTP.observations),
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "material_source_integrated_at_start": integrated,
        "material_module_runtime_role": "integrated" if integrated else "unintegrated module fingerprint only",
        "scope": "temporary synthetic SQLite; real loopback HTTP; free local provider; no real manuscript or credential",
        "claim_limit": "material dependency integrity only; no literary-quality or first-tier competitiveness claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
