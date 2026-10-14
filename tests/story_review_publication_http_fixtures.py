#!/usr/bin/env python3
"""Revision-bound semantic publication gates, using synthetic reports and zero models."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import moyu_analysis
import moyu_workspace
from publication_assistant_fixtures import PublicationAssistantFixtures, BODY_A, BODY_B
import server


QUOTE = "林岚数完台阶，才发现铜环不在原来的位置。"
REASON = "作者已查看当前稿件，确认这是有意保留的叙事安排。"
OVERRIDE_REASON = "Author confirmed the current manuscript and unresolved review."
SOURCES = ("server.py", "moyu_analysis.py", "moyu_workspace.py", __file__)


class StoryReviewPublicationHTTPTests(unittest.TestCase):
    evidence = []

    @classmethod
    def setUpClass(cls):
        PublicationAssistantFixtures.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        PublicationAssistantFixtures.tearDownClass.__func__(cls)

    request = PublicationAssistantFixtures.request
    snapshot = PublicationAssistantFixtures.snapshot
    stop_sentinels = PublicationAssistantFixtures.stop_sentinels
    count = PublicationAssistantFixtures.count

    def setUp(self):
        PublicationAssistantFixtures.setUp(self)
        for chapter_id in (self.chapter, self.second):
            status, changed = self.request("PATCH", "/api/chapters/" + chapter_id, {
                "pov": "全知视角", "scene_plan": "合成发布检查场景",
            }, self.project)
            self.assertEqual(status, 200, changed)
        self.run_basic_reviews()
        self.assert_publication(True)

    def save_report(self, *, empty=False, legacy=False, message="合成审稿问题，仅验证作者决定的出版门。"):
        with moyu_workspace.use_scope(self.project, self.branch, "fixture-author"):
            with server.connect() as conn:
                context, chapter = server.ai_context(conn, self.second, task="semantic_review")
                raw = {
                    "findings": [] if empty else [{
                        "category": "continuity", "severity": "possible", "message": message,
                        "suggestion": "等待作者明确处理；不评价此问题的真假。",
                        "evidence": [{"source_chapter_id": self.second, "source_quote": QUOTE}],
                    }],
                    "uncertainties": ["这是合成报告，不是模型审稿或文学效果证据。"],
                }
                report = moyu_analysis.save_review(conn, {
                    "project_id": self.project, "branch_id": self.branch, "sent_context": context,
                }, chapter, None, raw)
                self.sent_context = copy.deepcopy(context)
                if legacy:
                    stored = json.loads(conn.execute(
                        "SELECT payload_json FROM app_story_review WHERE id=?", (report["id"],),
                    ).fetchone()[0])
                    stored.pop("guidance_source")
                    conn.execute("UPDATE app_story_review SET payload_json=? WHERE id=?",
                                 (server.json_text(stored), report["id"]))
                    report.pop("guidance_source")
        return report

    def decide(self, report, decision, reason=REASON, resolution_source=None):
        payload = {"decision": decision, "reason": reason}
        if resolution_source is not None:
            payload["resolution_source"] = resolution_source
        return self.request("POST", (
            "/api/story-reviews/" + report["id"] + "/issues/" + report["findings"][0]["id"] + "/decision"
        ), payload, self.project)

    def reviews(self):
        status, payload = self.request("GET", "/api/story-reviews?chapter_id=" + self.second, project=self.project)
        self.assertEqual(status, 200, payload)
        return payload["reviews"]

    def run_basic_reviews(self):
        result = []
        for chapter_id in (self.chapter, self.second):
            status, checked = self.request("POST", "/api/review", {"chapter_id": chapter_id}, self.project)
            self.assertEqual(status, 200, checked)
            result.append(checked)
        return result

    def assert_gate(self, code):
        checked = self.run_basic_reviews()[1]
        self.assertEqual(checked["status"], "needs_revision", checked)
        self.assertIn(code, {issue["code"] for issue in checked["blocking"]}, checked)
        return checked

    def assert_publication(self, allowed):
        status, manifest = self.request("GET", "/api/export/manifest", project=self.project)
        self.assertEqual(status, 200, manifest)
        self.assertEqual(manifest["finalization"]["allowed"], allowed, manifest)
        self.assertFalse(manifest["finalization"]["overridden"])
        exports = []
        for fmt in ("markdown", "epub"):
            status, content = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True)
            self.assertEqual(status, 200 if allowed else 409, content)
            exports.append({"format": fmt, "http_status": status, "bytes": len(content) if isinstance(content, bytes) else None})
        self.evidence.append({
            "test": self._testMethodName, "allowed": allowed,
            "issues": manifest["finalization"]["issues"], "exports": exports,
        })
        return manifest

    def assert_commits(self, blocked, *, override=""):
        result = []
        for stage in ("prewrite", "precommit", "postcommit"):
            status, committed = self.request("POST", "/api/chapters/" + self.second + "/commit", {
                "stage": stage, "override_reason": override,
            }, self.project)
            self.assertEqual(status, 409 if blocked and not override else 201, committed)
            self.assertEqual(committed["status"], "blocked" if blocked and not override else "overridden" if blocked else "passed", committed)
            result.append({"stage": stage, "http_status": status, "status": committed["status"]})
        self.evidence.append({"test": self._testMethodName, "commits": result})
        return result

    def test_no_report_stays_free_and_does_not_require_semantic_review(self):
        self.assertEqual(self.reviews(), [])
        self.assert_commits(False)
        self.assert_publication(True)
        self.assertEqual(self.count("ai_run"), 0)
        self.assertEqual(self.count("app_ai_job"), 0)

    def test_empty_draft_empty_report_has_no_false_evidence_conflict_or_quality_claim(self):
        with moyu_workspace.use_scope(self.project, self.branch, "fixture-author"):
            with server.connect() as conn:
                draft = server.chapter_rows(conn)[-1]
                self.assertEqual(draft["body"], "")
                context, chapter = server.ai_context(conn, draft["id"], task="semantic_review")
                report = moyu_analysis.save_review(conn, {
                    "project_id": self.project, "branch_id": self.branch, "sent_context": context,
                }, chapter, None, {"findings": [], "uncertainties": ["空稿没有可引用原句。"]})
                self.assertEqual(report["status"], "no_findings_unverified")
                self.assertFalse(report["auto_pass"])
                self.assertFalse(report["literary_quality_verified"])
        self.assert_publication(True)

    def test_pending_and_author_accepted_issues_block_cached_review_all_stages_and_exports(self):
        report = self.save_report()
        status, state = self.request("GET", "/api/state", project=self.project)
        self.assertEqual(status, 200, state)
        self.assertEqual(next(chapter for chapter in state["chapters"] if chapter["id"] == self.second)["review_status"], "unreviewed")
        self.assert_gate("semantic_review_pending")
        self.assert_commits(True)
        self.assert_publication(False)
        status, decided = self.decide(report, "accepted")
        self.assertEqual(status, 200, decided)
        self.assertEqual(decided["review"]["status"], "needs_revision")
        self.assert_gate("semantic_review_needs_revision")
        self.assert_commits(True)
        self.assert_publication(False)
        self.assertEqual(self.count("ai_run"), 0)

    def test_current_reasoned_ignore_and_explanation_clear_the_gate(self):
        for decision in ("ignored", "explained"):
            with self.subTest(decision=decision):
                report = self.save_report(message="合成问题：" + decision)
                self.assertEqual(self.decide(report, decision, reason="")[0], 400)
                status, decided = self.decide(report, decision)
                self.assertEqual(status, 200, decided)
                self.assertEqual(decided["review"]["status"], "resolved_by_author")
                self.assertFalse(decided["review"]["literary_quality_verified"])
                self.assertEqual(self.run_basic_reviews()[1]["status"], "passed")
                self.assert_commits(False)
                self.assert_publication(True)

    def test_same_source_empty_rereview_does_not_erase_an_author_accepted_issue(self):
        report = self.save_report()
        self.assertEqual(self.decide(report, "accepted")[0], 200)
        empty = self.save_report(empty=True)
        self.assertEqual(empty["status"], "no_findings_unverified")
        self.assertFalse(empty["auto_pass"])
        self.assert_gate("semantic_review_needs_revision")
        self.assertEqual(self.reviews()[0]["id"], report["id"])
        status, decided = self.decide(report, "explained")
        self.assertEqual(status, 200, decided)
        self.assertEqual(len(decided["review"]["decision_log"]), 2)
        self.assertEqual(decided["review"]["decision_log"][1]["previous_decision"], "accepted")
        self.assertEqual(self.run_basic_reviews()[1]["status"], "passed")
        self.assert_publication(True)

    def test_changed_body_requires_new_review_or_free_current_bound_author_resolution(self):
        report = self.save_report()
        self.assertEqual(self.decide(report, "accepted")[0], 200)
        status, changed = self.request("PATCH", "/api/chapters/" + self.second, {
            "body": BODY_B + "\n作者补写了铜环去向。",
        }, self.project)
        self.assertEqual(status, 200, changed)
        stale = next(item for item in self.reviews() if item["id"] == report["id"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["findings"][0]["repair_targets"], [])
        checked = self.assert_gate("semantic_review_stale")
        self.assertNotIn("semantic_review_needs_revision", {issue["code"] for issue in checked["blocking"]})
        self.assert_commits(True)
        self.assert_publication(False)
        self.assertEqual(self.decide(stale, "explained")[0], 409)
        source = stale["author_resolution_source"]
        self.assertEqual(self.decide(stale, "accepted", resolution_source=source)[0], 409)
        self.assertEqual(self.decide(stale, "ignored", reason="", resolution_source=source)[0], 400)
        wrong = copy.deepcopy(source)
        wrong["source_revision_id"] = report["source_revision_id"]
        self.assertEqual(self.decide(stale, "explained", resolution_source=wrong)[0], 409)
        status, handled = self.decide(stale, "explained", resolution_source=source)
        self.assertEqual(status, 200, handled)
        self.assertTrue(handled["review"]["stale"])
        self.assertTrue(handled["review"]["findings"][0]["author_resolution_current"])
        self.assertEqual(handled["review"]["findings"][0]["decision"], "accepted")
        log_length = len(handled["review"]["decision_log"])
        status, repeated = self.decide(stale, "explained", resolution_source=source)
        self.assertEqual(status, 200, repeated)
        self.assertEqual(len(repeated["review"]["decision_log"]), log_length)
        self.assertEqual(self.run_basic_reviews()[1]["status"], "passed")
        self.assert_commits(False)
        self.assert_publication(True)
        status, changed = self.request("PATCH", "/api/chapters/" + self.second, {
            "body": BODY_B + "\n又发生一次改稿。",
        }, self.project)
        self.assertEqual(status, 200, changed)
        self.assert_gate("semantic_review_stale")
        self.assertEqual(self.decide(stale, "ignored", resolution_source=source)[0], 409)

    def test_successful_review_of_new_sources_replaces_stale_problem_without_permanent_lock(self):
        report = self.save_report()
        self.assertEqual(self.decide(report, "accepted")[0], 200)
        status, changed = self.request("PATCH", "/api/chapters/" + self.chapter, {
            "body": BODY_A + "\n前章证据已经改动。",
        }, self.project)
        self.assertEqual(status, 200, changed)
        self.assert_gate("semantic_review_stale")
        empty = self.save_report(empty=True)
        self.assertEqual(empty["status"], "no_findings_unverified")
        reports = self.reviews()
        prior = next(item for item in reports if item["id"] == report["id"])
        self.assertTrue(prior["stale"])
        self.assertEqual(prior["superseded_by"], empty["id"])
        self.assertEqual(reports[0]["id"], empty["id"])
        self.assertEqual(self.run_basic_reviews()[1]["status"], "passed")
        self.assert_commits(False)
        self.assert_publication(True)

    def test_guidance_changes_invalidate_review_and_reject_late_saved_result(self):
        report = self.save_report()
        self.assertIn("guidance_source", report)
        frozen = copy.deepcopy(self.sent_context)
        status, changed = self.request("PATCH", "/api/chapters/" + self.second, {
            "scene_plan": "作者改为在渡口等待，不进入北塔",
        }, self.project)
        self.assertEqual(status, 200, changed)
        stale = self.reviews()[0]
        self.assertTrue(stale["stale"])
        self.assert_gate("semantic_review_stale")
        with moyu_workspace.use_scope(self.project, self.branch, "fixture-author"):
            with server.connect() as conn:
                chapter = next(item for item in server.chapter_rows(conn) if item["id"] == self.second)
                with self.assertRaisesRegex(moyu_analysis.AnalysisConflict, "约定|计划"):
                    moyu_analysis.save_review(conn, {
                        "project_id": self.project, "branch_id": self.branch, "sent_context": frozen,
                    }, chapter, None, {"findings": [], "uncertainties": []})
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_story_review WHERE project_id=?", (self.project,)).fetchone()[0], 1)
        status, handled = self.decide(stale, "ignored", resolution_source=stale["author_resolution_source"])
        self.assertEqual(status, 200, handled)
        self.assertEqual(self.run_basic_reviews()[1]["status"], "passed")
        self.assert_publication(True)
        status, blueprint = self.request("GET", "/api/blueprint", project=self.project)
        self.assertEqual(status, 200, blueprint)
        status, updated = self.request("PUT", "/api/blueprint", {
            "expected_version": blueprint["version"], "blueprint": {"premise": "作者重新调整故事约定"},
        }, self.project)
        self.assertEqual(status, 200, updated)
        self.assert_gate("semantic_review_stale")
        self.assertFalse(self.reviews()[0]["findings"][0]["author_resolution_current"])
        self.assert_publication(False)

    def test_legacy_marked_report_uses_current_guidance_for_free_author_explanation(self):
        report = self.save_report(legacy=True)
        frozen = copy.deepcopy(self.sent_context)
        self.assertNotIn("guidance_source", report)
        self.assertIn("_guidance_source", frozen)
        self.assertEqual(report["material_source"], frozen["_material_source"])
        stale = self.reviews()[0]
        self.assertTrue(stale.get("stale"), stale)
        self.assertEqual(stale["findings"][0]["repair_targets"], [])
        baseline = stale["author_resolution_source"]
        with server.connect() as conn:
            self.assertEqual(baseline["guidance_source"], moyu_workspace.guidance_source(
                conn, self.project, self.branch, self.second))
        self.assertEqual(self.decide(stale, "accepted", resolution_source=baseline)[0], 409)
        self.assertEqual(self.decide(stale, "ignored", resolution_source=baseline)[0], 200)
        self.assertEqual(self.run_basic_reviews()[1]["status"], "passed")
        self.assert_publication(True)
        status, blueprint = self.request("GET", "/api/blueprint", project=self.project)
        self.assertEqual(status, 200, blueprint)
        self.assertEqual(self.request("PUT", "/api/blueprint", {
            "expected_version": blueprint["version"], "blueprint": {"premise": "旧报告没有冻结这个新字段"},
        }, self.project)[0], 200)
        renewed = self.reviews()[0]
        self.assertTrue(renewed.get("stale"), renewed)
        self.assertNotEqual(renewed["author_resolution_source"], baseline)
        self.assertFalse(renewed["findings"][0]["author_resolution_current"])
        self.assert_gate("semantic_review_stale")
        self.assert_publication(False)
        before = self.snapshot()
        self.assertEqual(self.decide(renewed, "ignored", resolution_source=baseline)[0], 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.decide(
            renewed, "ignored", resolution_source=renewed["author_resolution_source"],
        )[0], 200)
        self.assertTrue(self.reviews()[0]["findings"][0]["author_resolution_current"])
        self.assertEqual(self.run_basic_reviews()[1]["status"], "passed")
        self.assert_publication(True)
        self.assertEqual(self.sent_context, frozen)
        self.assertTrue(all(not mock.called for mock in self.mocks))
        self.evidence.append({
            "test": self._testMethodName, "synthetic_private_source_report": True,
            "actual_ai_run": False, "original_context_preserved": True,
            "missing_guidance_read_only": True, "old_resolution_http_status": 409,
            "current_resolution_http_status": 200, "provider_calls": 0,
        })

    def test_reasoned_override_is_preserved_in_commit_export_and_audit(self):
        report = self.save_report()
        self.assertEqual(self.decide(report, "accepted")[0], 200)
        self.assert_gate("semantic_review_needs_revision")
        for stage in ("prewrite", "precommit", "postcommit"):
            status, invalid = self.request("POST", "/api/chapters/" + self.second + "/commit", {
                "stage": stage, "override_reason": "1234567",
            }, self.project)
            self.assertEqual(status, 400, invalid)
        self.assert_commits(True, override=OVERRIDE_REASON)
        for fmt in ("markdown", "epub"):
            status, invalid = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True,
                                           headers={"X-Moyu-Export-Override": "1234567"})
            self.assertEqual(status, 409, invalid)
            status, exported = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True,
                                             headers={"X-Moyu-Export-Override": OVERRIDE_REASON})
            self.assertEqual(status, 200, exported)
            if fmt == "epub":
                with zipfile.ZipFile(io.BytesIO(exported)) as archive:
                    manifest = json.loads(archive.read("META-INF/moyu-export-manifest.json"))
                    self.assertTrue(manifest["finalization"]["overridden"])
                    self.assertTrue(any("需要修订" in issue for issue in manifest["finalization"]["issues"]))
                    self.assertEqual(manifest["author_override"]["reason"], OVERRIDE_REASON)
        status, events = self.request("GET", "/api/events", project=self.project)
        self.assertEqual(status, 200, events)
        exported_events = [event for event in events["events"] if event["event_type"] == "publication.exported"]
        self.assertTrue(exported_events)
        self.assertEqual(exported_events[0]["payload_json"]["author_override"]["reason"], OVERRIDE_REASON)
        self.assertEqual(self.reviews()[0]["status"], "needs_revision")

    def test_chinese_override_decodes_before_length_check_and_preserves_audit_reason(self):
        report = self.save_report()
        self.assertEqual(self.decide(report, "accepted")[0], 200)
        headers = {"X-Moyu-Export-Override": quote(REASON, safe="")}
        for fmt in ("markdown", "epub"):
            for invalid, expected in ((quote("一二三四五六七", safe=""), 409),
                                      ("%E4", 400), (quote("字" * 2001, safe=""), 400)):
                status, rejected = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True,
                                                headers={"X-Moyu-Export-Override": invalid})
                self.assertEqual(status, expected, rejected)
            status, exported = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True, headers=headers)
            self.assertEqual(status, 200, exported)
            if fmt == "epub":
                with zipfile.ZipFile(io.BytesIO(exported)) as archive:
                    manifest = json.loads(archive.read("META-INF/moyu-export-manifest.json"))
                    self.assertEqual(manifest["author_override"]["reason"], REASON)
                    self.assertTrue(manifest["finalization"]["overridden"])
        status, manifest = self.request("GET", "/api/export/manifest", project=self.project, headers=headers)
        self.assertEqual(status, 200, manifest)
        self.assertEqual(manifest["author_override"]["reason"], REASON)
        status, events = self.request("GET", "/api/events", project=self.project)
        self.assertEqual(status, 200, events)
        exported_events = [
            event for event in events["events"]
            if event["event_type"] == "publication.exported" and event["payload_json"].get("author_override")
        ]
        self.assertEqual(len(exported_events), 2)
        self.assertTrue(all(event["payload_json"]["author_override"]["reason"] == REASON for event in exported_events))
        self.evidence.append({
            "test": self._testMethodName, "utf8_percent_encoded_header": True,
            "decoded_chinese_reason_preserved": True, "seven_decoded_characters_http_status": 409,
            "invalid_utf8_http_status": 400, "too_long_decoded_reason_http_status": 400,
        })


def fingerprints():
    result = {}
    for name in SOURCES:
        path = Path(name)
        path = path if path.is_absolute() else ROOT / path
        result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if args.json_output and args.json_output.exists():
        raise SystemExit("Refusing to overwrite existing evidence.")
    before = fingerprints()
    outcome = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(StoryReviewPublicationHTTPTests))
    after = fingerprints()
    if args.json_output:
        record = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "scope": "isolated temporary database; real HTTP; synthetic reviews; no provider calls",
            "tests_run": outcome.testsRun, "successful": outcome.wasSuccessful(),
            "failures": len(outcome.failures), "errors": len(outcome.errors), "provider_calls": 0,
            "source_sha256_before": before, "source_sha256_after": after, "source_unchanged": before == after,
            "checks": StoryReviewPublicationHTTPTests.evidence,
            "boundary": "Tests consume synthetic review decisions and exact source dependencies, not model correctness or literary quality.",
        }
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
