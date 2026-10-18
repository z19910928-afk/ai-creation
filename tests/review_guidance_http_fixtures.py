#!/usr/bin/env python3
"""Saved reviews bind author guidance before decisions and local repairs."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import material_legacy_http_fixtures as legacy_fixture
from guidance_alias_http_fixtures import Provider
from guidance_scope_change_http_fixtures import EvidenceResult
from backend import moyu_analysis
from backend import moyu_workspace
from backend import server


PROJECT = "default-project"
SOURCES = (
    "backend/server.py", "backend/moyu_analysis.py", "backend/moyu_materials.py", "backend/moyu_context.py",
    "backend/moyu_workspace.py", "backend/moyu_records.py",
    "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py",
    "tests/material_legacy_http_fixtures.py",
    "tests/review_guidance_http_fixtures.py",
)


class ReviewGuidanceHTTP(unittest.TestCase):
    observations = []
    process = legacy_fixture.MaterialLegacyHTTP.process
    preview = legacy_fixture.MaterialLegacyHTTP.preview
    body = legacy_fixture.MaterialLegacyHTTP.body
    state = legacy_fixture.MaterialLegacyHTTP.state
    queue = legacy_fixture.MaterialLegacyHTTP.queue
    structured = legacy_fixture.MaterialLegacyHTTP.structured
    complete = legacy_fixture.MaterialLegacyHTTP.complete
    reviews = legacy_fixture.MaterialLegacyHTTP.reviews
    decide = legacy_fixture.MaterialLegacyHTTP.decide
    gate = legacy_fixture.MaterialLegacyHTTP.gate

    def setUp(self):
        self.exchanges = []
        legacy_fixture.MaterialLegacyHTTP.setUp(self)

    def tearDown(self):
        try:
            capture = os.environ.get("MOYU_REVIEW_GUIDANCE_CAPTURE")
            if capture:
                target = Path(capture) / self._testMethodName
                target.mkdir(parents=True, exist_ok=False)
                with server.connect() as source, sqlite3.connect(target / "synthetic-book.sqlite3") as dest:
                    source.backup(dest)
                (target / "http-and-provider.json").write_text(json.dumps({
                    "synthetic_only": True, "paid_provider_calls": 0,
                    "http": self.exchanges, "provider_requests": Provider.requests,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            legacy_fixture.MaterialLegacyHTTP.tearDown(self)

    def call(self, method, route, payload=None, project=PROJECT):
        status, response = legacy_fixture.MaterialLegacyHTTP.call(self, method, route, payload, project)
        self.exchanges.append({
            "method": method, "route": route, "project": project,
            "request": copy.deepcopy(payload), "status": status, "response": copy.deepcopy(response),
        })
        return status, response

    def observe(self, scenario, **values):
        self.observations.append({"test": self._testMethodName, "scenario": scenario, **values})

    def database_hash(self):
        with server.connect() as conn:
            return hashlib.sha256("\n".join(conn.iterdump()).encode("utf-8")).hexdigest()

    def frozen(self, job):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT j.context_json,r.consent_json FROM app_ai_job j "
                "JOIN ai_run r ON r.id=j.ai_run_id WHERE j.id=?", (job["id"],),
            ).fetchone()
        return dict(row)

    def stored(self, review):
        with server.connect() as conn:
            return json.loads(conn.execute(
                "SELECT payload_json FROM app_story_review WHERE id=?", (review["id"],),
            ).fetchone()[0])

    def current(self, review):
        return next(item for item in self.reviews() if item["id"] == review["id"])

    def strip_markers(self, review, *, without_material=False):
        with server.connect() as conn:
            payload = self.stored(review)
            payload.pop("guidance_source", None)
            payload.pop("decision_guidance_source", None)
            if without_material:
                payload.pop("material_source", None)
                payload.pop("decision_material_source", None)
            conn.execute("UPDATE app_story_review SET payload_json=? WHERE id=?",
                         (server.json_text(payload), review["id"]))
        return payload

    def guidance(self):
        with server.connect() as conn:
            return moyu_workspace.guidance_source(conn, PROJECT, "main", self.chapter_id)

    def blueprint_change(self, field, project=PROJECT):
        chapters = self.state()["chapters"]
        status, before = self.call("GET", "/api/blueprint", project=project)
        self.assertEqual(status, 200, before)
        blueprint = copy.deepcopy(before["blueprint"])
        blueprint[field] = {
            "premise": "合成新前提：窗前等候改为拒绝来客。",
            "contract": "合成新约定：隐藏来客身份，不提前泄露窗外线索。",
        }[field]
        materializations = sum(row["route"] == "/api/blueprint/materialize" for row in self.exchanges)
        status, after = self.call("PUT", "/api/blueprint", {
            "expected_version": before["version"], "blueprint": blueprint,
        }, project)
        self.assertEqual(status, 200, after)
        self.assertEqual(after["version"], before["version"] + 1)
        self.assertNotEqual(before["blueprint"][field], after["blueprint"][field])
        self.assertEqual({key: value for key, value in before["blueprint"].items() if key != field},
                         {key: value for key, value in after["blueprint"].items() if key != field})
        self.assertEqual(self.state()["chapters"], chapters)
        self.assertEqual(sum(row["route"] == "/api/blueprint/materialize" for row in self.exchanges),
                         materializations)

    def preservation(self, job, frozen, review, markers):
        current = self.frozen(job)
        stored = self.stored(review)
        return {
            "frozen_context_unchanged": current["context_json"] == frozen["context_json"],
            "consent_and_request_audit_unchanged": current["consent_json"] == frozen["consent_json"],
            "original_material_source_unchanged": stored.get("material_source") == markers.get("material_source"),
            "original_guidance_source_unchanged": stored.get("guidance_source") == markers.get("guidance_source"),
        }

    def repair_request(self, review, current):
        finding = current["findings"][0]
        source = finding["repair_targets"][0]["source"]
        return {
            "task": "polish", "chapter_id": source["chapter_id"], "source": copy.deepcopy(source),
            "review_source": {
                "review_id": review["id"], "issue_id": finding["id"], "evidence_index": 0,
            },
            "instruction": "审稿问题：" + finding["message"] + "\n修订建议：" + finding["suggestion"],
            "response_format": "text", "defer": True,
        }

    def direct_repair(self, request):
        with server.connect() as conn:
            return moyu_analysis.review_repair_source(conn, {
                "project_id": PROJECT, "branch_id": "main",
            }, request["review_source"])

    def save_late(self, job, context):
        raw = self.structured("semantic_review")
        raw["findings"][0]["message"] = "迟到的合成审稿，仅验证保存时的完整依据。"
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                chapter = next(item for item in server.chapter_rows(conn) if item["id"] == self.chapter_id)
                return moyu_analysis.save_review(conn, {
                    "project_id": PROJECT, "branch_id": "main", "sent_context": context,
                }, chapter, job["ai_run_id"], raw)

    def test_missing_guidance_with_real_run_is_read_only_before_any_other_change(self):
        job, _, review = self.complete("semantic_review")
        frozen = self.frozen(job)
        markers = self.strip_markers(review)
        before, calls = self.database_hash(), len(Provider.requests)
        current = self.current(review)
        preservation = self.preservation(job, frozen, review, markers)
        self.observe("missing_guidance_read_only", actual_saved_job_and_run=True,
                     material_source_present="material_source" in markers,
                     report_stale=bool(current.get("stale")),
                     repair_target_count=len(current["findings"][0]["repair_targets"]),
                     database_unchanged=self.database_hash() == before,
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertTrue(current.get("stale"), current)
        self.assertEqual(current["findings"][0]["repair_targets"], [])
        self.assertEqual(self.database_hash(), before)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def test_missing_guidance_rejects_acceptance_after_premise_only_change_atomically(self):
        job, _, review = self.complete("semantic_review")
        frozen = self.frozen(job)
        markers = self.strip_markers(review)
        self.blueprint_change("premise")
        current = self.current(review)
        before, chapters, calls = self.database_hash(), self.state()["chapters"], len(Provider.requests)
        status, response = self.decide(review, "accepted",
                                       resolution_source=current["author_resolution_source"])
        preservation = self.preservation(job, frozen, review, markers)
        self.observe("missing_guidance_acceptance", actual_saved_job_and_run=True,
                     chapter_body_revision_and_plan_unchanged=True, extra_materialize_calls=0,
                     report_stale=bool(current.get("stale")), accept_http_status=status,
                     database_unchanged=self.database_hash() == before,
                     chapters_unchanged=self.state()["chapters"] == chapters,
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertEqual(status, 409, response)
        self.assertTrue(current.get("stale"), current)
        self.assertEqual(self.database_hash(), before)
        self.assertEqual(self.state()["chapters"], chapters)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def test_missing_guidance_rejects_direct_and_queued_repair_before_new_job(self):
        job, _, review = self.complete("semantic_review")
        frozen = self.frozen(job)
        request = self.repair_request(review, self.current(review))
        markers = self.strip_markers(review)
        self.blueprint_change("premise")
        before, chapters, calls = self.database_hash(), self.state()["chapters"], len(Provider.requests)
        direct_rejected = False
        try:
            self.direct_repair(request)
        except moyu_analysis.AnalysisConflict:
            direct_rejected = True
        direct_unchanged = self.database_hash() == before
        status, response = self.call("POST", "/api/ai/queue", request)
        preservation = self.preservation(job, frozen, review, markers)
        self.observe("missing_guidance_repair", actual_saved_job_and_run=True,
                     direct_source_rejected=direct_rejected, direct_database_unchanged=direct_unchanged,
                     queue_http_status=status, queue_code=response.get("code"),
                     database_unchanged=self.database_hash() == before,
                     chapters_unchanged=self.state()["chapters"] == chapters,
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertTrue(direct_rejected)
        self.assertTrue(direct_unchanged)
        self.assertEqual(status, 409, response)
        self.assertEqual(response.get("code"), "review_source_stale")
        self.assertEqual(self.database_hash(), before)
        self.assertEqual(self.state()["chapters"], chapters)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def assert_late_save_rejected(self, *, missing):
        job, _, review = self.complete("semantic_review")
        frozen = self.frozen(job)
        context = json.loads(frozen["context_json"])
        self.assertIn("_material_source", context)
        self.assertIn("_guidance_source", context)
        if missing:
            context.pop("_guidance_source")
        self.blueprint_change("premise")
        before, calls = self.database_hash(), len(Provider.requests)
        rejected = False
        try:
            self.save_late(job, context)
        except moyu_analysis.AnalysisConflict:
            rejected = True
        preservation = self.preservation(job, frozen, review, review)
        self.observe("late_save_missing_guidance" if missing else "late_save_old_guidance",
                     actual_saved_job_and_run=True, material_source_present=True,
                     late_save_rejected=rejected, database_unchanged=self.database_hash() == before,
                     chapter_body_revision_and_plan_unchanged=True, extra_materialize_calls=0,
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertTrue(rejected)
        self.assertEqual(self.database_hash(), before)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def test_late_real_run_save_rejects_missing_guidance_atomically(self):
        self.assert_late_save_rejected(missing=True)

    def test_late_real_run_save_rejects_old_guidance_atomically(self):
        self.assert_late_save_rejected(missing=False)

    def assert_legacy_resolution_expires(self, field):
        job, _, review = self.complete("semantic_review")
        frozen = self.frozen(job)
        markers = self.strip_markers(review, without_material=True)
        current = self.current(review)
        self.assertTrue(current.get("stale"), current)
        baseline = copy.deepcopy(current["author_resolution_source"])
        calls = len(Provider.requests)
        first_status, first = self.decide(review, resolution_source=baseline)
        self.assertEqual(first_status, 200, first)
        self.assertEqual(self.gate()["issues"], [])
        self.blueprint_change(field)
        renewed = self.current(review)
        blocked = bool(self.gate()["issues"])
        before = self.database_hash()
        old_status, old = self.decide(review, resolution_source=baseline)
        rejected_unchanged = self.database_hash() == before
        new_status, new = self.decide(review, resolution_source=renewed["author_resolution_source"])
        final = self.current(review)
        preservation = self.preservation(job, frozen, review, markers)
        self.observe("legacy_resolution_guidance_changed", field=field,
                     actual_saved_job_and_run=True, original_both_markers_absent=True,
                     current_resolution_binds_guidance="guidance_source" in baseline,
                     first_explanation_http_status=first_status,
                     resolution_source_changed=renewed["author_resolution_source"] != baseline,
                     previous_explanation_current=renewed["findings"][0]["author_resolution_current"],
                     publication_blocked_after_change=blocked,
                     old_resolution_http_status=old_status,
                     old_resolution_rejection_atomic=rejected_unchanged,
                     new_resolution_http_status=new_status,
                     new_explanation_current=final["findings"][0]["author_resolution_current"],
                     publication_unblocked_after_current_explanation=not self.gate()["issues"],
                     chapter_body_revision_and_plan_unchanged=True, extra_materialize_calls=0,
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertIn("guidance_source", baseline)
        self.assertNotEqual(renewed["author_resolution_source"], baseline)
        self.assertFalse(renewed["findings"][0]["author_resolution_current"])
        self.assertTrue(blocked)
        self.assertEqual(old_status, 409, old)
        self.assertTrue(rejected_unchanged)
        self.assertEqual(new_status, 200, new)
        self.assertTrue(final["findings"][0]["author_resolution_current"])
        self.assertEqual(self.gate()["issues"], [])
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def test_legacy_free_explanation_expires_after_premise_only_change(self):
        self.assert_legacy_resolution_expires("premise")

    def test_legacy_free_explanation_expires_after_contract_only_change(self):
        self.assert_legacy_resolution_expires("contract")

    def test_current_sealed_review_still_accepts_and_queues_bound_repair(self):
        job, _, review = self.complete("semantic_review")
        frozen = self.frozen(job)
        current = self.current(review)
        self.assertFalse(current.get("stale"), current)
        self.assertEqual(current["guidance_source"], self.guidance())
        self.assertEqual(current["guidance_source"], json.loads(frozen["context_json"])["_guidance_source"])
        request = self.repair_request(review, current)
        calls = len(Provider.requests)
        direct = self.direct_repair(request)
        status, response = self.decide(review, "accepted")
        queue_status, queued = self.call("POST", "/api/ai/queue", request)
        preservation = self.preservation(job, frozen, review, review)
        self.observe("current_review_control", accept_http_status=status,
                     direct_bound_source_matches=direct["source"] == request["source"],
                     queue_http_status=queue_status, queued_task=queued.get("task"),
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertEqual(status, 200, response)
        self.assertEqual(response["review"]["status"], "needs_revision")
        self.assertEqual(direct["source"], request["source"])
        self.assertEqual(queue_status, 202, queued)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def test_other_book_blueprint_does_not_expire_free_current_legacy_explanation(self):
        job, _, review = self.complete("semantic_review")
        frozen = self.frozen(job)
        markers = self.strip_markers(review, without_material=True)
        baseline = self.current(review)["author_resolution_source"]
        self.assertEqual(self.decide(review, resolution_source=baseline)[0], 200)
        self.assertEqual(self.gate()["issues"], [])
        status, created = self.call("POST", "/api/projects", {"title": "隔离审稿指南作品"})
        self.assertEqual(status, 201, created)
        self.blueprint_change("contract", created["project"]["id"])
        calls, before = len(Provider.requests), self.database_hash()
        current = self.current(review)
        preservation = self.preservation(job, frozen, review, markers)
        self.observe("other_book_guidance_control",
                     resolution_source_unchanged=current["author_resolution_source"] == baseline,
                     explanation_current=current["findings"][0]["author_resolution_current"],
                     publication_unblocked=not self.gate()["issues"],
                     database_unchanged=self.database_hash() == before,
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertEqual(current["author_resolution_source"], baseline)
        self.assertTrue(current["findings"][0]["author_resolution_current"])
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(self.database_hash(), before)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def test_fake_run_without_private_context_preserves_component_contract(self):
        raw = self.structured("semantic_review")
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                chapter = next(item for item in server.chapter_rows(conn) if item["id"] == self.chapter_id)
                review = moyu_analysis.save_review(conn, {
                    "project_id": PROJECT, "branch_id": "main",
                }, chapter, "synthetic-review-component", raw)
                self.assertIsNone(conn.execute(
                    "SELECT id FROM ai_run WHERE id=?", (review["run_id"],),
                ).fetchone())
        current = self.current(review)
        request = self.repair_request(review, current)
        direct = self.direct_repair(request)
        status, response = self.decide(review, "accepted")
        self.observe("component_only_compatibility", actual_run_absent=True,
                     private_context_absent=True, report_stale=bool(current.get("stale")),
                     accept_http_status=status,
                     direct_bound_source_matches=direct["source"] == request["source"],
                     provider_calls=len(Provider.requests))
        self.assertNotIn("guidance_source", review)
        self.assertNotIn("material_source", review)
        self.assertFalse(current.get("stale"), current)
        self.assertEqual(status, 200, response)
        self.assertEqual(direct["source"], request["source"])
        self.assertEqual(len(Provider.requests), 0)

    def test_private_material_context_cannot_save_without_guidance_even_with_fake_run(self):
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                context, chapter = server.ai_context(conn, self.chapter_id, task="semantic_review")
        self.assertIn("_material_source", context)
        context.pop("_guidance_source")
        raw = self.structured("semantic_review")
        before, rejected = self.database_hash(), False
        try:
            with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
                with server.connect() as conn:
                    moyu_analysis.save_review(conn, {
                        "project_id": PROJECT, "branch_id": "main", "sent_context": context,
                    }, chapter, "synthetic-private-context", raw)
        except moyu_analysis.AnalysisConflict:
            rejected = True
        self.observe("private_marker_requires_guidance", actual_run_absent=True,
                     private_material_context_present=True, save_rejected=rejected,
                     database_unchanged=self.database_hash() == before,
                     provider_calls=len(Provider.requests))
        self.assertTrue(rejected)
        self.assertEqual(self.database_hash(), before)
        self.assertEqual(len(Provider.requests), 0)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def freeze_sources(phase, expected):
    capture = os.environ.get("MOYU_REVIEW_GUIDANCE_CAPTURE")
    if not capture:
        return False
    target = Path(capture) / ("source-" + phase)
    target.mkdir(parents=True, exist_ok=False)
    for name in SOURCES:
        content = (ROOT / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected[name]:
            raise RuntimeError("Source changed before capture: " + name)
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (target / "fingerprints.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main():
    output = os.environ.get("MOYU_REVIEW_GUIDANCE_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    before_capture = freeze_sources("before", before)
    Provider.total_calls = 0
    ReviewGuidanceHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReviewGuidanceHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    after_capture = freeze_sources("after", after)
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(ReviewGuidanceHTTP.observations),
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "frozen_sources_before_captured": before_capture, "frozen_sources_after_captured": after_capture,
        "source_unchanged": before == after,
        "scope": "real saved AI jobs and runs in temporary synthetic SQLite; loopback HTTP; free local provider",
        "claim_limit": "saved review guidance and author-decision integrity only; no real manuscript, credential, literary-quality or first-tier claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
