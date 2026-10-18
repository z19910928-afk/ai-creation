#!/usr/bin/env python3
"""Persisted nested ownership restrictions remain independent hard limits."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from guidance_scope_change_http_fixtures import (
    EvidenceResult, GuidanceScopeChangeHTTP, Provider,
)
from backend import moyu_records
from backend import server


PROJECT = "default-project"
CANARY = "NESTED_RECORD_BOUNDARY_CANARY"
SOURCES = (
    "backend/moyu_context.py", "backend/moyu_records.py", "backend/moyu_workspace.py", "backend/server.py",
    "backend/moyu_backups.py", "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py", "tests/nested_record_scope_http_fixtures.py",
)


class NestedRecordScopeHTTP(unittest.TestCase):
    observations = []
    setUp = GuidanceScopeChangeHTTP.setUp
    tearDown = GuidanceScopeChangeHTTP.tearDown
    call = GuidanceScopeChangeHTTP.call
    queue = GuidanceScopeChangeHTTP.queue
    process = GuidanceScopeChangeHTTP.process
    preview = GuidanceScopeChangeHTTP.preview
    body = GuidanceScopeChangeHTTP.body
    state = GuidanceScopeChangeHTTP.state
    ledger = GuidanceScopeChangeHTTP.ledger
    observe = GuidanceScopeChangeHTTP.observe

    def card(self, *, project=PROJECT, **declaration):
        status, item = self.call("POST", "/api/records", {
            "type": "设定", "name": "嵌套范围验证", "description": CANARY, **declaration,
        }, project)
        self.assertEqual(status, 201, item)
        return item

    def assert_filtered(self, scenario):
        preview = self.preview()
        job = self.queue()
        completed = self.process(job)
        sent = json.dumps(Provider.requests[-1], ensure_ascii=False)
        self.observe(scenario, fresh_preview_excludes_card=CANARY not in preview["user"],
                     current_dispatch_status=completed["status"],
                     actual_sent_request_excludes_card=CANARY not in sent,
                     source_unchanged=self.body() == self.start_body)
        self.assertNotIn(CANARY, preview["user"])
        self.assertEqual(completed["status"], "success", completed)
        self.assertNotIn(CANARY, sent)
        self.assertEqual(self.body(), self.start_body)

    def test_nested_project_declaration_blocks_local_project_card(self):
        self.start_body = self.body()
        card = self.card(knowledge_scope={"project_id": "outside_project"})
        self.assertEqual(card["knowledge_scope"]["project_id"], "outside_project")
        self.assert_filtered("nested_project")

    def test_nested_branch_declaration_blocks_local_branch_card(self):
        self.start_body = self.body()
        card = self.card(knowledge_scope={"branch_id": "outside_branch"})
        self.assertEqual(card["knowledge_scope"]["branch_id"], "outside_branch")
        self.assert_filtered("nested_branch")

    def test_matching_top_branch_cannot_override_conflicting_nested_branch(self):
        self.start_body = self.body()
        self.card(branch_id="main", knowledge_scope={"branch_id": "outside_branch"})
        self.assert_filtered("matching_top_conflicting_nested")

    def test_matching_nested_branch_cannot_override_conflicting_top_branch(self):
        self.start_body = self.body()
        self.card(branch_id="outside_branch", knowledge_scope={"branch_id": "main"})
        self.assert_filtered("conflicting_top_matching_nested")

    def test_matching_nested_ownership_keeps_preview_send_and_apply_connected(self):
        self.card(branch_id="main", knowledge_scope={"project_id": PROJECT, "branch_id": "main"})
        preview, body = self.preview(), self.body()
        job = self.queue()
        completed = self.process(job)
        sent = Provider.requests[0]["messages"][-1]["content"]
        status, applied = self.call("POST", "/api/ai/runs/" + job["ai_run_id"] + "/apply", {
            **job["source"], "decision": "accepted",
            "replacement": "\n\n" + completed["result"]["text"],
        })
        self.observe("matching_nested", preview_contains_card=CANARY in preview["user"],
                     preview_equals_sent_user=preview["user"] == sent,
                     current_dispatch_status=completed["status"], apply_status=status,
                     applied=applied.get("applied"), body_changed=self.body() != body)
        self.assertIn(CANARY, preview["user"])
        self.assertEqual(preview["user"], sent)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["applied"])
        self.assertNotEqual(self.body(), body)

    def test_matching_nested_limits_still_obey_branches_array(self):
        self.start_body = self.body()
        self.card(knowledge_scope={"project_id": PROJECT, "branch_id": "main",
                                  "branches": ["outside_branch"]})
        self.assert_filtered("nested_match_branches_conflict")

    def test_matching_nested_limits_still_obey_branch_ids_array(self):
        self.start_body = self.body()
        self.card(knowledge_scope={"branch_id": "main", "branch_ids": ["outside_branch"]})
        self.assert_filtered("nested_match_branch_ids_conflict")

    def test_matching_nested_limits_still_obey_private_viewpoint(self):
        self.start_body = self.body()
        self.card(knowledge_scope={"project_id": PROJECT, "branch_id": "main",
                                  "private": True, "known_by": ["outside_person"]})
        self.assert_filtered("nested_match_private_conflict")

    def test_matching_nested_limits_still_obey_chapter_timing(self):
        self.start_body = self.body()
        self.card(knowledge_scope={"project_id": PROJECT, "branch_id": "main",
                                  "available_from": 9})
        self.assert_filtered("nested_match_future_chapter")

    def test_foreign_owned_card_cannot_be_pinned_by_matching_nested_declaration(self):
        status, other = self.call("POST", "/api/projects", {"title": "嵌套范围另一作品"})
        self.assertEqual(status, 201, other)
        card = self.card(project=other["project"]["id"], knowledge_scope={"project_id": PROJECT,
                                                                       "branch_id": "main"})
        ledger = self.ledger()
        request = {**self.request, "material_controls": {"records": {card["id"]: {"pin": True}}}}
        outcomes = []
        for route in ("/api/ai/prompt-preview", "/api/ai/queue"):
            status, response = self.call("POST", route, {**request, "defer": True})
            outcomes.append({"route": route, "status": status})
        self.observe("foreign_ownership", outcomes=outcomes,
                     ledger_unchanged=self.ledger() == ledger, provider_calls=len(Provider.requests))
        self.assertEqual([row["status"] for row in outcomes], [400, 400], response)
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(len(Provider.requests), 0)
        self.assertNotIn(CANARY, self.preview()["user"])

    def test_legacy_sync_and_description_edit_preserve_conflicting_nested_limit(self):
        card = self.card(knowledge_scope={"project_id": PROJECT, "branch_id": "outside_branch"})
        status, edited = self.call("PATCH", "/api/records/" + card["id"], {
            "description": CANARY + "：旧客户端只修改说明。",
        })
        self.assertEqual(status, 200, edited)
        state = self.state()
        for item in state["records"]:
            for key in moyu_records.FIELDS:
                item.pop(key, None)
        status, saved = self.call("PUT", "/api/state", state)
        self.assertEqual(status, 200, saved)
        retained = next(item for item in saved["records"] if item["id"] == card["id"])
        self.assertEqual(retained["knowledge_scope"], card["knowledge_scope"])
        self.start_body = self.body()
        self.assert_filtered("legacy_nested_limit_preserved")

    def test_copy_and_backup_remap_matching_nested_identifiers(self):
        card = self.card(knowledge_scope={"project_id": PROJECT, "branch_id": "main",
                                         "branches": ["main"], "known_by": [self.person["id"]]})
        source = self.state()
        status, copied = self.call("POST", f"/api/projects/{PROJECT}/duplicate", {"title": "嵌套范围副本"})
        self.assertEqual(status, 201, copied)
        status, backup = self.call("POST", "/api/backups", {})
        self.assertEqual(status, 201, backup)
        self.assertTrue(backup["verified"])
        status, restored = self.call("POST", "/api/backups/" + backup["id"] + "/restore", {"confirm": True})
        self.assertEqual(status, 201, restored)
        for name, project in (("copy", copied["project"]), ("backup", restored["project"])):
            with self.subTest(recovery=name):
                pid = project["id"]
                state = self.state(pid)
                actual = next(item for item in state["records"] if item["name"] == card["name"])
                person = next(item for item in state["records"] if item["name"] == self.person["name"])
                declaration = actual["knowledge_scope"]
                self.assertEqual(declaration["project_id"], pid)
                self.assertEqual(declaration["branch_id"], state["branch_id"])
                self.assertEqual(declaration["branches"], [state["branch_id"]])
                self.assertEqual(declaration["known_by"], [person["id"]])
                chapter = next(item for item in state["chapters"] if item["body"] == self.body())
                status, preview = self.call("POST", "/api/ai/prompt-preview", {
                    **self.request, "chapter_id": chapter["id"],
                }, pid)
                self.observe(name, ownership_remapped=True, branch_array_remapped=True,
                             known_by_remapped=True, preview_status=status,
                             preview_contains_allowed_card=CANARY in preview.get("user", ""))
                self.assertEqual(status, 200, preview)
                self.assertIn(CANARY, preview["user"])
        self.assertEqual(self.state()["records"], source["records"])
        self.assertEqual(len(Provider.requests), 0)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def main():
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    Provider.total_calls = 0
    NestedRecordScopeHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NestedRecordScopeHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": NestedRecordScopeHTTP.observations,
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "scope": "temporary synthetic DB; actual HTTP; matching ownership and independent nested declarations",
    }
    output = os.environ.get("MOYU_NESTED_SCOPE_REPORT")
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
