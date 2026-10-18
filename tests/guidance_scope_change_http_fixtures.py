#!/usr/bin/env python3
"""Frozen writing requests respect later knowledge-boundary changes."""
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

from guidance_alias_http_fixtures import GuidanceAliasHTTP, Provider
from backend import moyu_analysis
from backend import moyu_records
from backend import moyu_workspace
from backend.moyu_provider import ProviderError
from backend import server


CANARY = "SCOPE_CHANGE_CARD_CANARY"
PROJECT = "default-project"
SOURCES = (
    "backend/moyu_workspace.py", "backend/moyu_context.py", "backend/moyu_records.py", "backend/moyu_analysis.py",
    "backend/moyu_backups.py", "backend/server.py", "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py",
)


class GuidanceScopeChangeHTTP(unittest.TestCase):
    observations = []
    queue = GuidanceAliasHTTP.queue
    process = GuidanceAliasHTTP.process
    preview = GuidanceAliasHTTP.preview
    body = GuidanceAliasHTTP.body
    ledger = GuidanceAliasHTTP.ledger

    def setUp(self):
        self.exchanges = []
        GuidanceAliasHTTP.setUp(self)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)

    def tearDown(self):
        capture = os.environ.get("MOYU_GUIDANCE_SCOPE_CAPTURE")
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

    def card(self, **declaration):
        status, item = self.call("POST", "/api/records", {
            "type": "设定", "name": "范围验证材料", "description": CANARY, **declaration,
        })
        self.assertEqual(status, 201, item)
        return item

    def change(self, card, project=PROJECT, **declaration):
        status, item = self.call("PATCH", "/api/records/" + card["id"], declaration, project)
        self.assertEqual(status, 200, item)
        return item

    def state(self, project=PROJECT):
        status, state = self.call("GET", "/api/state", project=project)
        self.assertEqual(status, 200, state)
        return state

    def source(self, project=PROJECT, chapter_id=None):
        with server.connect() as conn:
            scope = moyu_workspace.resolve_scope(conn, project)
            return moyu_workspace.guidance_source(conn, *scope[:2], chapter_id or self.chapter_id)

    def review(self, frozen=None):
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                context, chapter = server.ai_context(conn, self.chapter_id, task="semantic_review")
                raw = {
                    "findings": [{
                        "category": "continuity", "severity": "possible",
                        "message": "合成问题，仅验证来源变化后的决定有效性。",
                        "suggestion": "由作者决定；不是文学质量证据。",
                        "evidence": [{
                            "source_chapter_id": self.chapter_id,
                            "source_start": 0, "source_end": 19,
                            "source_quote": chapter["body"][:19],
                        }],
                    }],
                    "uncertainties": ["合成审稿，没有模型调用。"],
                }
                return moyu_analysis.save_review(conn, {
                    "project_id": PROJECT, "branch_id": "main",
                    "sent_context": frozen if frozen is not None else context,
                }, chapter, None, raw)

    def reviews(self):
        status, response = self.call("GET", "/api/story-reviews?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, response)
        return response["reviews"]

    def decide(self, report, **data):
        return self.call("POST", "/api/story-reviews/" + report["id"] + "/issues/" +
                         report["findings"][0]["id"] + "/decision", {
                             "decision": "explained", "reason": "作者核对了当前材料与稿件。",
                             **data,
                         })

    def gate(self):
        with server.connect() as conn:
            return moyu_analysis.review_publication_gate(conn, {
                "project_id": PROJECT, "branch_id": "main",
            }, self.chapter_id)

    def test_scope_restrictions_block_frozen_dispatch_before_provider(self):
        declarations = {
            "nested_future_chapter": {"knowledge_scope": {"available_from": 9}},
            "top_future_chapter": {"available_from": 9},
            "private_other_viewpoint": {"knowledge_scope": {
                "private": True, "known_by": ["scope_other_person"],
            }},
            "private_without_owner": {"private": True},
            "other_branch": {"knowledge_scope": {"branches": ["scope_other_branch"]}},
            "future_valid_state": {"valid_start": 9},
            "author_plan": {"epistemic_mode": "author_plan"},
        }
        for name, declaration in declarations.items():
            with self.subTest(scope=name):
                card = self.card()
                self.assertIn(CANARY, self.preview()["user"])
                job = self.queue()
                previous_source, body, calls = self.source(), self.body(), len(Provider.requests)
                self.change(card, **declaration)
                excluded = CANARY not in self.preview()["user"]
                completed = self.process(job)
                self.observe(name, fresh_preview_excludes_card=excluded,
                             source_changed=self.source() != previous_source,
                             dispatch_status=completed["status"],
                             billing_state=completed.get("result", {}).get("billing_state"),
                             extra_provider_calls=len(Provider.requests) - calls,
                             body_unchanged=self.body() == body)
                self.assertTrue(excluded)
                self.assertEqual(completed["status"], "failed", completed)
                self.assertEqual(completed["result"]["billing_state"], "not_dispatched")
                self.assertEqual(len(Provider.requests), calls)
                self.assertEqual(self.body(), body)

    def test_scope_change_rejects_late_frozen_job_creation_atomically(self):
        card = self.card()
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                frozen, chapter = server.ai_context(conn, self.chapter_id, task="continue")
        self.change(card, knowledge_scope={"available_from": 9})
        ledger = self.ledger()
        # Delayed assembly simulates the scope being saved before job insertion.
        with patch("backend.server.ai_context", return_value=(frozen, chapter)):
            status, rejected = self.call("POST", "/api/ai/queue", {**self.request, "defer": True})
        self.observe("late_creation", http_status=status, ledger_unchanged=self.ledger() == ledger,
                     provider_calls=len(Provider.requests))
        self.assertEqual(status, 400, rejected)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(len(Provider.requests), 0)

    def test_canceled_original_retries_reject_new_scope_without_new_tasks(self):
        card = self.card()
        job = self.queue()
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.change(card, private=True)
        ledger, body = self.ledger(), self.body()
        outcomes = []
        with patch("backend.server.start_ai_worker", return_value=False):
            for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, response = self.call("POST", f"/api/ai/{kind}/{identifier}/retry", {"mode": "original"})
                outcomes.append({"entry": kind, "status": status})
        self.observe("canceled_retry", outcomes=outcomes, ledger_unchanged=self.ledger() == ledger,
                     body_unchanged=self.body() == body, provider_calls=len(Provider.requests))
        self.assertEqual([row["status"] for row in outcomes], [409, 409], response)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), 0)

    def test_failed_original_retries_reject_changed_known_by(self):
        card = self.card(knowledge_scope={"known_by": [self.person["id"]], "private": True})
        job = self.queue()
        failure = ProviderError("synthetic failure before dispatch", code="fixture_not_dispatched",
                                request_dispatched=False)
        with patch("backend.server.call_ai_provider", side_effect=failure):
            self.assertEqual(self.process(job)["status"], "failed")
        self.change(card, knowledge_scope={"known_by": ["scope_other_person"], "private": True})
        ledger = self.ledger()
        outcomes = []
        with patch("backend.server.start_ai_worker", return_value=False):
            for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, response = self.call("POST", f"/api/ai/{kind}/{identifier}/retry", {"mode": "original"})
                outcomes.append({"entry": kind, "status": status})
        self.observe("failed_retry", outcomes=outcomes, ledger_unchanged=self.ledger() == ledger,
                     provider_calls=len(Provider.requests))
        self.assertEqual([row["status"] for row in outcomes], [409, 409], response)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(len(Provider.requests), 0)

    def test_successful_old_candidate_cannot_apply_after_scope_change(self):
        card = self.card()
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.assertIn(CANARY, json.dumps(Provider.requests[0], ensure_ascii=False))
        self.change(card, knowledge_scope={"branches": ["scope_other_branch"]})
        body, ledger = self.body(), self.ledger()
        status, result = self.call("POST", "/api/ai/runs/" + job["ai_run_id"] + "/apply", {
            **job["source"], "decision": "accepted", "replacement": "\n\n" + completed["result"]["text"],
        })
        self.observe("old_candidate_apply", http_status=status, applied=result.get("applied"),
                     body_unchanged=self.body() == body, ledger_unchanged=self.ledger() == ledger,
                     provider_calls=len(Provider.requests))
        self.assertEqual(status, 400, result)
        self.assertEqual(self.body(), body)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(len(Provider.requests), 1)

    def test_scope_change_expires_saved_author_decision_and_late_semantic_result(self):
        card = self.card()
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                frozen, _ = server.ai_context(conn, self.chapter_id, task="semantic_review")
        report = self.review(frozen)
        status, handled = self.decide(report)
        self.assertEqual(status, 200, handled)
        self.assertEqual(handled["review"]["status"], "resolved_by_author")
        self.change(card, available_from=9)
        stale = self.reviews()[0]
        late_saved = False
        try:
            self.review(frozen)
            late_saved = True
        except moyu_analysis.AnalysisConflict:
            pass
        gate_codes = [issue["code"] for issue in self.gate()["issues"]]
        self.observe("semantic_source_changed", report_stale=stale.get("stale", False),
                     old_decision=stale["findings"][0].get("decision"),
                     repair_targets=stale["findings"][0].get("repair_targets"),
                     late_result_saved=late_saved, gate_codes=gate_codes)
        self.assertTrue(stale.get("stale"), stale)
        self.assertEqual(stale["findings"][0]["decision"], "explained")
        self.assertEqual(stale["findings"][0]["repair_targets"], [])
        self.assertIn("semantic_review_stale", gate_codes)
        self.assertFalse(late_saved)
        self.assertEqual(len(Provider.requests), 0)

    def test_scope_change_expires_existing_current_bound_stale_resolution(self):
        card = self.card()
        report = self.review()
        self.assertEqual(self.decide(report, decision="accepted")[0], 200)
        self.assertEqual(self.call("PATCH", "/api/chapters/" + self.chapter_id, {
            "scene_plan": "作者调整为雨夜窗外的等候。",
        })[0], 200)
        stale = self.reviews()[0]
        self.assertTrue(stale["stale"])
        resolution = stale["author_resolution_source"]
        status, handled = self.decide(stale, resolution_source=resolution)
        self.assertEqual(status, 200, handled)
        self.assertTrue(handled["review"]["findings"][0]["author_resolution_current"])
        self.assertEqual(self.gate()["issues"], [])
        log = copy.deepcopy(handled["review"]["decision_log"])
        self.change(card, knowledge_scope={"available_from": 9})
        changed = self.reviews()[0]
        status, rejected = self.decide(changed, resolution_source=resolution)
        codes = [issue["code"] for issue in self.gate()["issues"]]
        self.observe("stale_resolution_changed", old_resolution_current=
                     changed["findings"][0]["author_resolution_current"],
                     old_resolution_reuse_status=status, gate_codes=codes,
                     decision_log_preserved=changed["decision_log"] == log)
        self.assertFalse(changed["findings"][0]["author_resolution_current"])
        self.assertEqual(status, 409, rejected)
        self.assertEqual(changed["decision_log"], log)
        self.assertIn("semantic_review_stale", codes)
        status, renewed = self.decide(changed, resolution_source=changed["author_resolution_source"])
        self.assertEqual(status, 200, renewed)
        self.assertTrue(renewed["review"]["findings"][0]["author_resolution_current"])
        self.assertEqual(renewed["review"]["findings"][0]["decision"], "accepted")
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(len(Provider.requests), 0)

    def test_new_request_uses_current_scope_and_can_still_apply(self):
        card = self.card()
        self.change(card, knowledge_scope={"available_from": 9})
        self.assertNotIn(CANARY, self.preview()["user"])
        job = self.queue()
        completed = self.process(job)
        body = self.body()
        status, applied = self.call("POST", "/api/ai/runs/" + job["ai_run_id"] + "/apply", {
            **job["source"], "decision": "accepted", "replacement": "\n\n" + completed["result"]["text"],
        })
        self.observe("new_current_request", dispatch_status=completed["status"],
                     provider_excludes_card=CANARY not in json.dumps(Provider.requests, ensure_ascii=False),
                     apply_http_status=status, applied=applied.get("applied"), body_changed=self.body() != body)
        self.assertEqual(completed["status"], "success", completed)
        self.assertNotIn(CANARY, json.dumps(Provider.requests, ensure_ascii=False))
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])
        self.assertNotEqual(self.body(), body)

    def test_legacy_description_sync_preserves_scope_and_blocks_old_materials(self):
        declaration = {"knowledge_scope": {"known_by": [self.person["id"]], "private": True,
                                           "available_from": 1}}
        card = self.card(**declaration)
        job, source, body = self.queue(), self.source(), self.body()
        edited = self.change(card, description=CANARY + "：作者仅补写说明。")
        self.assertEqual(edited["knowledge_scope"], declaration["knowledge_scope"])
        legacy = self.state()
        for item in legacy["records"]:
            for key in moyu_records.FIELDS:
                item.pop(key, None)
        status, saved = self.call("PUT", "/api/state", legacy)
        self.assertEqual(status, 200, saved)
        restored = next(item for item in saved["records"] if item["id"] == card["id"])
        completed = self.process(job)
        self.observe("legacy_preserves_scope", source_unchanged=self.source() == source,
                     saved_scope_preserved=restored["knowledge_scope"] == declaration["knowledge_scope"],
                     dispatch_status=completed["status"],
                     billing_state=completed.get("result", {}).get("billing_state"),
                     provider_calls=len(Provider.requests), body_unchanged=self.body() == body)
        self.assertEqual(restored["knowledge_scope"], declaration["knowledge_scope"])
        self.assertEqual(self.source(), source)
        self.assertEqual(completed["status"], "failed", completed)
        self.assertIn("资料", completed.get("error", ""))
        self.assertEqual(completed["result"]["billing_state"], "not_dispatched")
        self.assertEqual(len(Provider.requests), 0)
        self.assertEqual(self.body(), body)

    def test_legacy_omitted_description_sync_keeps_saved_boundary_and_task_usable(self):
        declaration = {"knowledge_scope": {"known_by": [self.person["id"]], "private": True,
                                           "available_from": 1}}
        card = self.card(**declaration)
        self.request["material_controls"] = {"records": {card["id"]: {"omit": True}}}
        self.assertNotIn(CANARY, self.preview()["user"])
        job, source = self.queue(), self.source()
        self.change(card, description=CANARY + "：未消费资料已补写说明。")
        legacy = self.state()
        for item in legacy["records"]:
            for key in moyu_records.FIELDS:
                item.pop(key, None)
        status, saved = self.call("PUT", "/api/state", legacy)
        self.assertEqual(status, 200, saved)
        restored = next(item for item in saved["records"] if item["id"] == card["id"])
        completed = self.process(job)
        self.observe("legacy_omitted_scope_control", source_unchanged=self.source() == source,
                     saved_scope_preserved=restored["knowledge_scope"] == declaration["knowledge_scope"],
                     dispatch_status=completed["status"], provider_calls=len(Provider.requests),
                     provider_excludes_card=CANARY not in json.dumps(Provider.requests, ensure_ascii=False))
        self.assertEqual(restored["knowledge_scope"], declaration["knowledge_scope"])
        self.assertEqual(self.source(), source)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertNotIn(CANARY, json.dumps(Provider.requests, ensure_ascii=False))

    def test_equivalent_saved_json_and_missing_empty_projection_are_stable(self):
        card = self.card(knowledge_scope={"private": True, "known_by": [self.person["id"]],
                                         "available_from": 1})
        job, source = self.queue(), self.source()
        with server.connect() as conn:
            equivalent = json.dumps({"knowledge_scope": {
                "available_from": 1, "known_by": [self.person["id"]], "private": True,
            }}, indent=4)
            conn.execute("UPDATE app_entity_scope SET scope_json=? WHERE entity_id=?",
                         (equivalent, card["id"]))
        completed = self.process(job)
        self.observe("equivalent_json", source_unchanged=self.source() == source,
                     dispatch_status=completed["status"])
        self.assertEqual(self.source(), source)
        self.assertEqual(completed["status"], "success", completed)
        empty_card = self.card()
        with server.connect() as conn:
            conn.execute("DELETE FROM app_entity_scope WHERE entity_id=?", (empty_card["id"],))
        job, source = self.queue(), self.source()
        with server.connect() as conn:
            moyu_records.write_scope(conn, empty_card["id"], {}, server.now())
        completed = self.process(job)
        self.observe("missing_and_empty", source_unchanged=self.source() == source,
                     dispatch_status=completed["status"])
        self.assertEqual(self.source(), source)
        self.assertEqual(completed["status"], "success", completed)

    def test_corrupt_saved_boundaries_reject_already_frozen_dispatch(self):
        card = self.card()
        broken_scopes = ("[]", '{"known_by":"scope_other_person"}', '{"private":"yes"}', "broken-json")
        jobs = [self.queue() for _ in broken_scopes]
        for raw, job in zip(broken_scopes, jobs):
            with self.subTest(saved_scope=raw):
                with server.connect() as conn:
                    conn.execute("UPDATE app_entity_scope SET scope_json=? WHERE entity_id=?",
                                 (raw, card["id"]))
                calls = len(Provider.requests)
                completed = self.process(job)
                self.observe("corrupt_saved_boundary", invalid_json=raw, dispatch_status=completed["status"],
                             extra_provider_calls=len(Provider.requests) - calls,
                             error=completed.get("error", ""))
                self.assertEqual(completed["status"], "failed", completed)
                self.assertIn("可见范围损坏", completed["error"])
                self.assertEqual(completed["result"]["billing_state"], "not_dispatched")
                self.assertEqual(len(Provider.requests), calls)
        with server.connect() as conn:
            moyu_records.write_scope(conn, card["id"], {}, server.now())

    def test_other_project_scope_changes_do_not_expire_this_request(self):
        self.card()
        job, source = self.queue(), self.source()
        status, other = self.call("POST", "/api/projects", {"title": "范围隔离的另一部作品"})
        self.assertEqual(status, 201, other)
        status, card = self.call("POST", "/api/records", {
            "type": "设定", "name": "别作范围", "description": CANARY,
            "knowledge_scope": {"available_from": 9},
        }, other["project"]["id"])
        self.assertEqual(status, 201, card)
        self.change(card, other["project"]["id"], knowledge_scope={"available_from": 10})
        completed = self.process(job)
        self.observe("other_project", source_unchanged=self.source() == source,
                     dispatch_status=completed["status"], provider_calls=len(Provider.requests))
        self.assertEqual(self.source(), source)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)

    def test_copies_and_backups_remap_scope_and_protect_new_frozen_requests(self):
        card = self.card(knowledge_scope={"known_by": [self.person["id"]], "private": True,
                                         "available_from": 1, "branches": ["main"]})
        original_state, source = self.state(), self.source()
        original_job = self.queue()
        status, copied = self.call("POST", f"/api/projects/{PROJECT}/duplicate", {"title": "范围副本"})
        self.assertEqual(status, 201, copied)
        status, backup = self.call("POST", "/api/backups", {})
        self.assertEqual(status, 201, backup)
        self.assertTrue(backup["verified"])
        status, recovered = self.call("POST", "/api/backups/" + backup["id"] + "/restore", {"confirm": True})
        self.assertEqual(status, 201, recovered)
        for kind, project in (("copy", copied["project"]), ("backup", recovered["project"])):
            with self.subTest(recovery=kind):
                pid = project["id"]
                state = self.state(pid)
                person = next(item for item in state["records"] if item["name"] == self.person["name"])
                scope_card = next(item for item in state["records"] if item["name"] == card["name"])
                chapter = next(item for item in state["chapters"] if item["body"] == self.body())
                self.assertEqual(scope_card["knowledge_scope"]["known_by"], [person["id"]])
                self.assertEqual(scope_card["knowledge_scope"]["branches"], [state["branch_id"]])
                status, settings = self.call("PUT", "/api/ai/settings", {
                    "base_url": f"http://127.0.0.1:{self.provider.server_port}/v1",
                    "model": "guidance-alias-fixture", "api_key": "", "daily_limit": 20,
                }, pid)
                self.assertEqual(status, 200, settings)
                request = {**self.request, "chapter_id": chapter["id"]}
                status, preview = self.call("POST", "/api/ai/prompt-preview", request, pid)
                self.assertEqual(status, 200, preview)
                self.assertIn(CANARY, preview["user"])
                status, job = self.call("POST", "/api/ai/queue", {**request, "defer": True}, pid)
                self.assertEqual(status, 202, job)
                self.change(scope_card, pid, knowledge_scope={"available_from": 9})
                status, excluded = self.call("POST", "/api/ai/prompt-preview", request, pid)
                self.assertEqual(status, 200, excluded)
                calls = len(Provider.requests)
                status, completed = self.call("POST", "/api/ai/jobs/" + job["id"] + "/process", {}, pid)
                self.observe(kind, remapped_owner=True, remapped_branch=True,
                             preview_excludes_card=CANARY not in excluded["user"],
                             dispatch_http_status=status, dispatch_status=completed.get("status"),
                             extra_provider_calls=len(Provider.requests) - calls)
                self.assertNotIn(CANARY, excluded["user"])
                self.assertEqual(status, 200, completed)
                self.assertEqual(completed["status"], "failed", completed)
                self.assertEqual(len(Provider.requests), calls)
        self.assertEqual(self.state()["records"], original_state["records"])
        self.assertEqual(self.source(), source)
        self.assertEqual(self.process(original_job)["status"], "success")


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def record(self, test, passed, error=None, subtest=None):
        case = {"name": test.id().rsplit(".", 1)[-1], "passed": passed}
        if subtest is not None:
            case["subtest"] = str(subtest)
        if error:
            case["error"] = self._exc_info_to_string(error, test)
        self.cases.append(case)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, True)

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.record(test, False, error)

    def addError(self, test, error):
        super().addError(test, error)
        self.record(test, False, error)

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        self.record(test, error is None, error, subtest)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def main():
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    Provider.total_calls = 0
    GuidanceScopeChangeHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GuidanceScopeChangeHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": GuidanceScopeChangeHTTP.observations,
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "scope": "temporary synthetic databases; real local HTTP; free local provider; no real manuscript or credential",
    }
    output = os.environ.get("MOYU_GUIDANCE_SCOPE_REPORT")
    if output:
        target = Path(output)
        if target.exists():
            raise RuntimeError("Evidence exists; use a new report path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
