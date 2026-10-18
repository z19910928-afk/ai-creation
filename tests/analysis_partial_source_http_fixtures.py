#!/usr/bin/env python3
"""Partial persisted model sources never fall back to component compatibility."""
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
from review_guidance_http_fixtures import ReviewGuidanceHTTP
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
    "tests/analysis_partial_source_http_fixtures.py",
)


class AnalysisPartialSourceHTTP(unittest.TestCase):
    observations = []
    preview = legacy_fixture.MaterialLegacyHTTP.preview
    body = legacy_fixture.MaterialLegacyHTTP.body
    state = legacy_fixture.MaterialLegacyHTTP.state
    change = legacy_fixture.MaterialLegacyHTTP.change
    structured = legacy_fixture.MaterialLegacyHTTP.structured
    analyses = legacy_fixture.MaterialLegacyHTTP.analyses
    confirm = legacy_fixture.MaterialLegacyHTTP.confirm
    reviews = legacy_fixture.MaterialLegacyHTTP.reviews
    decide = legacy_fixture.MaterialLegacyHTTP.decide
    gate = legacy_fixture.MaterialLegacyHTTP.gate
    database_hash = ReviewGuidanceHTTP.database_hash
    guidance = ReviewGuidanceHTTP.guidance
    repair_request = ReviewGuidanceHTTP.repair_request
    direct_repair = ReviewGuidanceHTTP.direct_repair

    def setUp(self):
        self.exchanges = []
        self.frozen_contexts = []
        legacy_fixture.MaterialLegacyHTTP.setUp(self)

    def tearDown(self):
        try:
            capture = os.environ.get("MOYU_ANALYSIS_PARTIAL_SOURCE_CAPTURE")
            if capture:
                target = Path(capture) / self._testMethodName
                target.mkdir(parents=True, exist_ok=False)
                with server.connect() as source, sqlite3.connect(target / "synthetic-book.sqlite3") as dest:
                    source.backup(dest)
                (target / "http-and-provider.json").write_text(json.dumps({
                    "synthetic_only": True, "paid_provider_calls": 0,
                    "http": self.exchanges, "frozen_contexts": self.frozen_contexts,
                    "provider_requests": Provider.requests,
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

    def save(self, task, *, private_context=True):
        raw = self.structured(task)
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                chapter = next(item for item in server.chapter_rows(conn) if item["id"] == self.chapter_id)
                scope = {"project_id": PROJECT, "branch_id": "main"}
                if private_context:
                    context, chapter = server.ai_context(conn, self.chapter_id, task=task)
                    scope["sent_context"] = context
                    self.frozen_contexts.append(copy.deepcopy(context))
                function = moyu_analysis.save_analysis if task == "chapter_analysis" else moyu_analysis.save_review
                item = function(conn, scope, chapter, None, raw)
                self.assertIsNone(item["run_id"])
        if private_context:
            self.assertEqual(item["material_source"], context["_material_source"])
            self.assertEqual(item["guidance_source"], context["_guidance_source"])
        return item

    def table(self, task):
        return "app_chapter_analysis" if task == "chapter_analysis" else "app_story_review"

    def stored(self, task, item):
        with server.connect() as conn:
            return json.loads(conn.execute(
                "SELECT payload_json FROM " + self.table(task) + " WHERE id=?", (item["id"],),
            ).fetchone()[0])

    def partial(self, task, item, variant):
        payload = self.stored(task, item)
        if variant == "guidance_only":
            payload.pop("material_source")
        elif variant in {"decision_material_only", "decision_guidance_only"}:
            name = "material_source" if variant == "decision_material_only" else "guidance_source"
            payload["decision_" + name] = copy.deepcopy(payload[name])
            payload.pop("material_source")
            payload.pop("guidance_source")
        elif variant in {"decision_material_null", "decision_guidance_null"}:
            name = "material_source" if variant == "decision_material_null" else "guidance_source"
            payload["decision_" + name] = None
        else:
            raise ValueError("unknown partial-source variant")
        with server.connect() as conn:
            conn.execute("UPDATE " + self.table(task) + " SET payload_json=? WHERE id=?",
                         (server.json_text(payload), item["id"]))
        return payload

    def current(self, task, item):
        listed = self.analyses() if task == "chapter_analysis" else self.reviews()
        return next(report for report in listed if report["id"] == item["id"])

    def confirm_all(self, item):
        return self.confirm(item, confirm_summary=True, confirm_handoff=True, decisions=[
            {"candidate_id": candidate["id"], "decision": "accepted"}
            for candidate in item["candidates"]
        ])

    def assert_analysis_protected(self, variant):
        task = "chapter_analysis"
        item = self.save(task)
        frozen = copy.deepcopy(self.frozen_contexts)
        payload = self.partial(task, item, variant)
        before, chapters, calls = self.database_hash(), self.state()["chapters"], len(Provider.requests)
        current = self.current(task, item)
        read_unchanged = self.database_hash() == before
        status, response = self.confirm_all(item)
        self.observe("partial_analysis_protection", variant=variant,
                     actual_ai_run_absent=True, persisted_private_source_present=True,
                     report_status=current["status"], report_stale=bool(current.get("stale")),
                     get_database_unchanged=read_unchanged, confirm_http_status=status,
                     database_unchanged=self.database_hash() == before,
                     chapters_unchanged=self.state()["chapters"] == chapters,
                     frozen_context_unchanged=self.frozen_contexts == frozen,
                     stored_sources_unchanged=all(self.stored(task, item).get(key) == payload.get(key)
                         for key in ("material_source", "guidance_source", "decision_material_source",
                                     "decision_guidance_source")),
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(current["status"], "stale", current)
        self.assertTrue(current.get("stale"), current)
        self.assertTrue(read_unchanged)
        self.assertEqual(status, 409, response)
        self.assertEqual(self.database_hash(), before)
        self.assertEqual(self.state()["chapters"], chapters)
        self.assertEqual(self.frozen_contexts, frozen)
        self.assertEqual(self.stored(task, item), payload)
        self.assertEqual(len(Provider.requests), calls)

    def assert_review_protected(self, variant):
        task = "semantic_review"
        item = self.save(task)
        frozen = copy.deepcopy(self.frozen_contexts)
        request = self.repair_request(item, self.current(task, item))
        payload = self.partial(task, item, variant)
        before, chapters, calls = self.database_hash(), self.state()["chapters"], len(Provider.requests)
        current = self.current(task, item)
        read_unchanged = self.database_hash() == before
        direct_rejected = False
        try:
            self.direct_repair(request)
        except moyu_analysis.AnalysisConflict:
            direct_rejected = True
        direct_unchanged = self.database_hash() == before
        accept_status, accepted = self.decide(item, "accepted",
                                            resolution_source=current["author_resolution_source"])
        accept_unchanged = self.database_hash() == before
        queue_status, queued = self.call("POST", "/api/ai/queue", request)
        self.observe("partial_review_protection", variant=variant,
                     actual_ai_run_absent=True, persisted_private_source_present=True,
                     report_stale=bool(current.get("stale")),
                     repair_target_count=len(current["findings"][0]["repair_targets"]),
                     get_database_unchanged=read_unchanged, direct_repair_rejected=direct_rejected,
                     direct_database_unchanged=direct_unchanged,
                     accept_http_status=accept_status, accept_database_unchanged=accept_unchanged,
                     queue_http_status=queue_status, queue_code=queued.get("code"),
                     database_unchanged=self.database_hash() == before,
                     chapters_unchanged=self.state()["chapters"] == chapters,
                     frozen_context_unchanged=self.frozen_contexts == frozen,
                     stored_sources_unchanged=all(self.stored(task, item).get(key) == payload.get(key)
                         for key in ("material_source", "guidance_source", "decision_material_source",
                                     "decision_guidance_source")),
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertTrue(current.get("stale"), current)
        self.assertEqual(current["findings"][0]["repair_targets"], [])
        self.assertTrue(read_unchanged)
        self.assertTrue(direct_rejected)
        self.assertTrue(direct_unchanged)
        self.assertEqual(accept_status, 409, accepted)
        self.assertTrue(accept_unchanged)
        self.assertEqual(queue_status, 409, queued)
        self.assertEqual(queued.get("code"), "review_source_stale")
        self.assertEqual(self.database_hash(), before)
        self.assertEqual(self.state()["chapters"], chapters)
        self.assertEqual(self.frozen_contexts, frozen)
        self.assertEqual(self.stored(task, item), payload)
        self.assertEqual(len(Provider.requests), calls)

    def test_guidance_only_analysis_is_read_only_and_cannot_confirm(self):
        self.assert_analysis_protected("guidance_only")

    def test_guidance_only_review_cannot_accept_or_repair(self):
        self.assert_review_protected("guidance_only")

    def test_guidance_only_free_explanation_binds_current_materials_and_expires(self):
        task = "semantic_review"
        item = self.save(task)
        frozen = copy.deepcopy(self.frozen_contexts)
        payload = self.partial(task, item, "guidance_only")
        current = self.current(task, item)
        baseline = copy.deepcopy(current["author_resolution_source"])
        guidance = self.guidance()
        calls = len(Provider.requests)
        first_status, first = self.decide(item, resolution_source=baseline)
        self.assertEqual(first_status, 200, first)
        self.assertEqual(self.gate()["issues"], [])
        self.change(description="合成当前资料已更正，人物名称、别名和范围保持。")
        self.assertEqual(self.guidance(), guidance)
        renewed = self.current(task, item)
        blocked = bool(self.gate()["issues"])
        before = self.database_hash()
        old_status, old = self.decide(item, resolution_source=baseline)
        rejected_unchanged = self.database_hash() == before
        new_status, new = self.decide(item, resolution_source=renewed["author_resolution_source"])
        final = self.current(task, item)
        self.observe("partial_author_resolution_materials", actual_ai_run_absent=True,
                     original_guidance_kept=True, original_material_absent=True,
                     current_material_snapshot_bound="legacy_material_source" in baseline,
                     guidance_unchanged_by_description_change=self.guidance() == guidance,
                     first_explanation_http_status=first_status,
                     resolution_source_changed=renewed["author_resolution_source"] != baseline,
                     previous_explanation_current=renewed["findings"][0]["author_resolution_current"],
                     publication_blocked_after_material_change=blocked,
                     old_resolution_http_status=old_status,
                     old_resolution_rejection_atomic=rejected_unchanged,
                     new_resolution_http_status=new_status,
                     publication_unblocked_after_current_explanation=not self.gate()["issues"],
                     current_explanation_current=final["findings"][0]["author_resolution_current"],
                     frozen_context_unchanged=self.frozen_contexts == frozen,
                     original_guidance_unchanged=self.stored(task, item).get("guidance_source")
                     == payload["guidance_source"], extra_provider_calls=len(Provider.requests) - calls)
        self.assertIn("legacy_material_source", baseline)
        self.assertEqual(baseline["legacy_material_source"]["purpose"], "author_resolution")
        self.assertEqual(baseline["guidance_source"], guidance)
        self.assertNotEqual(renewed["author_resolution_source"], baseline)
        self.assertFalse(renewed["findings"][0]["author_resolution_current"])
        self.assertTrue(blocked)
        self.assertEqual(old_status, 409, old)
        self.assertTrue(rejected_unchanged)
        self.assertEqual(new_status, 200, new)
        self.assertTrue(final["findings"][0]["author_resolution_current"])
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(self.frozen_contexts, frozen)
        self.assertEqual(self.stored(task, item).get("guidance_source"), payload["guidance_source"])
        self.assertNotIn("material_source", self.stored(task, item))
        self.assertEqual(len(Provider.requests), calls)

    def test_orphan_decision_material_cannot_replace_original_sources(self):
        for task in ("chapter_analysis", "semantic_review"):
            with self.subTest(task=task):
                if task == "chapter_analysis":
                    self.assert_analysis_protected("decision_material_only")
                else:
                    self.assert_review_protected("decision_material_only")

    def test_orphan_decision_guidance_cannot_replace_original_sources(self):
        for task in ("chapter_analysis", "semantic_review"):
            with self.subTest(task=task):
                if task == "chapter_analysis":
                    self.assert_analysis_protected("decision_guidance_only")
                else:
                    self.assert_review_protected("decision_guidance_only")

    def test_explicit_null_decision_material_is_not_compatible(self):
        for task in ("chapter_analysis", "semantic_review"):
            with self.subTest(task=task):
                if task == "chapter_analysis":
                    self.assert_analysis_protected("decision_material_null")
                else:
                    self.assert_review_protected("decision_material_null")

    def test_explicit_null_decision_guidance_is_not_compatible(self):
        for task in ("chapter_analysis", "semantic_review"):
            with self.subTest(task=task):
                if task == "chapter_analysis":
                    self.assert_analysis_protected("decision_guidance_null")
                else:
                    self.assert_review_protected("decision_guidance_null")

    def test_components_without_any_private_source_keep_normal_controls(self):
        analysis = self.save("chapter_analysis", private_context=False)
        self.assertNotIn("material_source", analysis)
        self.assertNotIn("guidance_source", analysis)
        status, response = self.confirm_all(analysis)
        self.assertEqual(status, 200, response)
        self.assertEqual(self.current("chapter_analysis", analysis)["status"], "reviewed")
        review = self.save("semantic_review", private_context=False)
        self.assertNotIn("material_source", review)
        self.assertNotIn("guidance_source", review)
        current = self.current("semantic_review", review)
        self.assertFalse(current.get("stale"), current)
        request = self.repair_request(review, current)
        direct = self.direct_repair(request)
        accept_status, accepted = self.decide(review, "accepted")
        queue_status, queued = self.call("POST", "/api/ai/queue", request)
        self.observe("pure_component_controls", actual_ai_run_absent=True,
                     private_context_absent=True, analysis_confirm_http_status=status,
                     review_accept_http_status=accept_status, review_repair_queue_http_status=queue_status,
                     direct_bound_source_matches=direct["source"] == request["source"],
                     provider_calls=len(Provider.requests))
        self.assertEqual(accept_status, 200, accepted)
        self.assertEqual(queue_status, 202, queued)
        self.assertEqual(direct["source"], request["source"])
        self.assertEqual(len(Provider.requests), 0)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def freeze_sources(phase, expected):
    capture = os.environ.get("MOYU_ANALYSIS_PARTIAL_SOURCE_CAPTURE")
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
    output = os.environ.get("MOYU_ANALYSIS_PARTIAL_SOURCE_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    before_capture = freeze_sources("before", before)
    Provider.total_calls = 0
    AnalysisPartialSourceHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisPartialSourceHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    after_capture = freeze_sources("after", after)
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(AnalysisPartialSourceHTTP.observations),
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "frozen_sources_before_captured": before_capture, "frozen_sources_after_captured": after_capture,
        "source_unchanged": before == after,
        "scope": "synthetic reports with server-private contexts but no AI run; temporary SQLite; real loopback HTTP; no provider calls",
        "claim_limit": "partial persisted source and component compatibility integrity only; no real manuscript, credential, literary-quality or first-tier claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
