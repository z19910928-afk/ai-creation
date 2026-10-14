#!/usr/bin/env python3
"""Real saved AI histories remain read-only without their material seal."""
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

from guidance_alias_http_fixtures import GuidanceAliasHTTP, Provider, encode
from guidance_scope_change_http_fixtures import EvidenceResult
import moyu_analysis
import moyu_workspace
import server


PROJECT = "default-project"
SOURCES = (
    "server.py", "moyu_analysis.py", "moyu_materials.py", "moyu_context.py",
    "moyu_workspace.py", "moyu_records.py", "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py", "tests/material_legacy_http_fixtures.py",
)


class MaterialLegacyHTTP(unittest.TestCase):
    observations = []
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

    def change(self, record=None, **fields):
        record = record or self.person
        status, item = self.call("PATCH", "/api/records/" + record["id"], fields)
        self.assertEqual(status, 200, item)
        return item

    def ledger(self):
        with server.connect() as conn:
            return {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                    for table in ("app_ai_job", "ai_run", "app_quota_reservation",
                                  "candidate_memory", "assertion", "character_state",
                                  "document_revision", "event_log")}

    def queue(self, task="continue"):
        status, job = self.call("POST", "/api/ai/queue", {
            **self.request, "task": task, "response_format": "text" if task == "continue" else "json",
            "defer": True,
        })
        self.assertEqual(status, 202, job)
        return job

    def structured(self, task):
        quote = self.body()[:19]
        evidence = {
            "source_chapter_id": self.chapter_id, "source_quote": quote,
            "source_start": 0, "source_end": 19,
        }
        if task == "semantic_review":
            return {
                "findings": [{
                    "category": "continuity", "severity": "possible",
                    "message": "合成审稿问题，仅验证历史材料保护。",
                    "suggestion": "由作者核对当前稿件。",
                    "evidence": [evidence],
                }],
                "uncertainties": ["免费本机合成材料，不证明文学效果。"],
            }
        return {
            "summary": {"text": "她在北窗听雨。", "evidence": [evidence]},
            "handoff": {"text": "继续等待窗外的来客。", "evidence": [evidence]},
            "candidates": [
                {
                    "kind": "event", "name": "窗前听雨", "note": "林岚在窗前听雨。",
                    "subject_name": self.person["name"], "epistemic_mode": "world_truth",
                    "known_by": [self.person["id"]], **evidence,
                },
                {
                    "kind": "character_state", "name": "窗扣", "note": "指尖抵着窗扣。",
                    "subject_name": self.person["name"], "attribute": "动作", "value": "抵着窗扣",
                    "epistemic_mode": "world_truth", "known_by": [self.person["id"]], **evidence,
                },
            ],
        }

    def complete(self, task):
        structured = self.structured(task)
        job = self.queue(task)

        def serve(handler):
            payload = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
            Provider.requests.append(payload)
            Provider.total_calls += 1
            raw = encode({
                "model": "guidance-alias-fixture",
                "choices": [{
                    "message": {"content": json.dumps(structured, ensure_ascii=False)},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110},
            })
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)

        with patch.object(Provider, "do_POST", serve):
            completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        key = "analysis" if task == "chapter_analysis" else "story_review"
        report = completed["result"][key]
        self.assertEqual(report["run_id"], job["ai_run_id"])
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM ai_run WHERE id=?",
                                          (report["run_id"],)).fetchone()[0], "success")
            self.assertEqual(conn.execute("SELECT status FROM app_ai_job WHERE ai_run_id=?",
                                          (report["run_id"],)).fetchone()[0], "success")
        return job, completed, report

    def legacy(self, task, *, without_guidance=False):
        job, completed, report = self.complete(task)
        table = "app_chapter_analysis" if task == "chapter_analysis" else "app_story_review"
        with server.connect() as conn:
            row = conn.execute(
                "SELECT j.context_json,r.consent_json FROM app_ai_job j "
                "JOIN ai_run r ON r.id=j.ai_run_id WHERE j.id=?", (job["id"],)).fetchone()
            context, consent = json.loads(row["context_json"]), json.loads(row["consent_json"])
            context.pop("_material_source")
            if without_guidance:
                context.pop("_guidance_source", None)
            conn.execute("UPDATE app_ai_job SET context_json=? WHERE id=?",
                         (server.json_text(context), job["id"]))
            stored = json.loads(conn.execute("SELECT payload_json FROM " + table + " WHERE id=?",
                                             (report["id"],)).fetchone()[0])
            stored.pop("material_source")
            stored.pop("decision_material_source", None)
            if without_guidance:
                stored.pop("guidance_source", None)
            conn.execute("UPDATE " + table + " SET payload_json=? WHERE id=?",
                         (server.json_text(stored), report["id"]))
            saved = json.loads(conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                           (job["ai_run_id"],)).fetchone()[0])
            self.assertEqual(saved["request_audit"], consent["request_audit"])
        return job, completed, stored

    def analyses(self):
        status, response = self.call("GET", "/api/chapter-analysis?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, response)
        return response["analyses"]

    def confirm(self, analysis, **fields):
        return self.call("POST", "/api/chapter-analysis/" + analysis["id"] + "/confirm", {
            "source_revision_id": analysis["source_revision_id"],
            "chapter_hash": analysis["chapter_hash"], "decisions": [], **fields,
        })

    def reviews(self):
        status, response = self.call("GET", "/api/story-reviews?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, response)
        return response["reviews"]

    def decide(self, review, decision="explained", **fields):
        return self.call("POST", "/api/story-reviews/" + review["id"] + "/issues/" +
                         review["findings"][0]["id"] + "/decision", {
            "decision": decision, "reason": "作者已核对当前合成稿件和资料。", **fields,
        })

    def gate(self):
        with server.connect() as conn:
            return moyu_analysis.review_publication_gate(conn, {
                "project_id": PROJECT, "branch_id": "main",
            }, self.chapter_id)

    def assert_legacy_analysis_protected(self, changed):
        job, _, analysis = self.legacy("chapter_analysis")
        if changed:
            self.change(description="旧任务完成后作者已修改人物资料。")
        previous_state, previous_ledger, calls = self.state(), self.ledger(), len(Provider.requests)
        saved = self.analyses()[0]
        status, response = self.confirm(analysis, confirm_summary=True)
        self.observe("legacy_analysis_changed" if changed else "legacy_analysis_unchanged",
                     actual_ai_run=True, actual_saved_job=True, analysis_status=saved["status"],
                     confirm_http_status=status, state_unchanged=self.state() == previous_state,
                     ledger_unchanged=self.ledger() == previous_ledger,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(saved["status"], "stale", saved)
        self.assertEqual(status, 409, response)
        self.assertEqual(self.state(), previous_state)
        self.assertEqual(self.ledger(), previous_ledger)
        self.assertEqual(len(Provider.requests), calls)
        read_status, history = self.call("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(read_status, 200, history)
        self.assertTrue(history["materials_read_only"])
        self.assertTrue(history["result"]["text"])

    def test_legacy_analysis_with_actual_run_is_read_only_without_other_changes(self):
        self.assert_legacy_analysis_protected(False)

    def test_legacy_analysis_with_actual_run_rejects_confirmation_after_material_change(self):
        self.assert_legacy_analysis_protected(True)

    def test_legacy_review_allows_free_current_explanation_but_not_old_evidence_acceptance(self):
        _, _, review = self.legacy("semantic_review")
        calls = len(Provider.requests)
        stale = self.reviews()[0]
        self.observe("legacy_review_unchanged", actual_ai_run=True, actual_saved_job=True,
                     stale=bool(stale.get("stale")), repair_targets=stale["findings"][0]["repair_targets"])
        self.assertTrue(stale.get("stale"), stale)
        self.assertEqual(stale["findings"][0]["repair_targets"], [])
        source = stale["author_resolution_source"]
        self.assertIsInstance(source, dict)
        self.assertTrue(self.gate()["issues"])
        status, response = self.decide(review, "accepted", resolution_source=source)
        self.assertEqual(status, 409, response)
        status, response = self.decide(review, resolution_source=source)
        self.assertEqual(status, 200, response)
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(len(Provider.requests), calls)

    def test_legacy_review_explanation_rechecks_current_material_absent_from_old_request(self):
        _, _, review = self.legacy("semantic_review")
        status, new_record = self.call("POST", "/api/records", {
            "type": "设定", "name": "旧任务未见的新设定", "description": "作者解释时的当前材料。",
        })
        self.assertEqual(status, 201, new_record)
        stale = self.reviews()[0]
        self.assertTrue(stale.get("stale"), stale)
        status, response = self.decide(review, resolution_source=stale["author_resolution_source"])
        self.assertEqual(status, 200, response)
        self.assertEqual(self.gate()["issues"], [])
        old_source = stale["author_resolution_source"]
        self.change(new_record, description="作者解释之后改变的新设定。")
        renewed = self.reviews()[0]
        self.observe("legacy_resolution_current_material", new_record_was_not_in_frozen_request=True,
                     resolution_changed=renewed["author_resolution_source"] != old_source,
                     explanation_current=renewed["findings"][0]["author_resolution_current"],
                     publication_blocked=bool(self.gate()["issues"]))
        self.assertFalse(renewed["findings"][0]["author_resolution_current"])
        self.assertNotEqual(renewed["author_resolution_source"], old_source)
        self.assertTrue(self.gate()["issues"])
        status, response = self.decide(review, resolution_source=old_source)
        self.assertEqual(status, 409, response)

    def test_legacy_resolution_snapshot_tracks_added_and_deleted_same_book_records(self):
        _, _, review = self.legacy("semantic_review", without_guidance=True)
        stale = self.reviews()[0]
        baseline = stale["author_resolution_source"]
        self.assertIn("legacy_material_source", baseline)
        self.assertIn("guidance_source", baseline)
        with server.connect() as conn:
            self.assertEqual(baseline["guidance_source"], moyu_workspace.guidance_source(
                conn, PROJECT, "main", self.chapter_id))
        self.assertEqual(self.decide(review, resolution_source=baseline)[0], 200)
        self.assertEqual(self.gate()["issues"], [])
        status, added = self.call("POST", "/api/records", {
            "type": "设定", "name": "解释后新加入的设定", "description": "当前资料集合增加了内容。",
        })
        self.assertEqual(status, 201, added)
        with_added = self.reviews()[0]
        self.assertFalse(with_added["findings"][0]["author_resolution_current"])
        self.assertNotEqual(with_added["author_resolution_source"], baseline)
        self.assertTrue(self.gate()["issues"])
        renewed_source = with_added["author_resolution_source"]
        self.assertEqual(self.decide(review, resolution_source=renewed_source)[0], 200)
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(self.call("DELETE", "/api/records/" + added["id"], {})[0], 200)
        with_deleted = self.reviews()[0]
        self.assertFalse(with_deleted["findings"][0]["author_resolution_current"])
        self.assertNotEqual(with_deleted["author_resolution_source"], renewed_source)
        self.assertTrue(self.gate()["issues"])
        self.assertEqual(len(Provider.requests), 1)
        self.observe("legacy_snapshot_membership", original_guidance_absent=True,
                     current_guidance_bound=True,
                     added_material_invalidated=True, deleted_material_invalidated=True,
                     extra_provider_calls=0)

    def test_legacy_resolution_snapshot_ignores_other_book_material_changes(self):
        _, _, review = self.legacy("semantic_review", without_guidance=True)
        baseline = self.reviews()[0]["author_resolution_source"]
        self.assertEqual(self.decide(review, resolution_source=baseline)[0], 200)
        self.assertEqual(self.gate()["issues"], [])
        status, other = self.call("POST", "/api/projects", {"title": "隔离的另一部合成作品"})
        self.assertEqual(status, 201, other)
        other_id = other["project"]["id"]
        status, record = self.call("POST", "/api/records", {
            "type": "设定", "name": "他书设定", "description": "仅属于另一部作品。",
        }, other_id)
        self.assertEqual(status, 201, record)
        self.assertEqual(self.call("PATCH", "/api/records/" + record["id"], {
            "description": "另一部作品的资料已修改。",
        }, other_id)[0], 200)
        self.assertEqual(self.call("DELETE", "/api/records/" + record["id"], {}, other_id)[0], 200)
        current = self.reviews()[0]
        self.assertEqual(current["author_resolution_source"], baseline)
        self.assertTrue(current["findings"][0]["author_resolution_current"])
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(len(Provider.requests), 1)
        self.observe("legacy_snapshot_other_book_control", guidance_absent=True,
                     other_book_added_changed_deleted=True, source_unchanged=True,
                     explanation_current=True, publication_unblocked=True, extra_provider_calls=0)

    def test_current_analysis_self_confirmations_and_candidates_remain_usable(self):
        _, _, analysis = self.complete("chapter_analysis")
        original_marker = copy.deepcopy(analysis["material_source"])
        for fields in (
            {"confirm_summary": True}, {"confirm_summary": True}, {"confirm_handoff": True},
            {"decisions": [{"candidate_id": analysis["candidates"][0]["id"], "decision": "accepted"}]},
            {"decisions": [{"candidate_id": analysis["candidates"][1]["id"], "decision": "accepted"}]},
        ):
            status, response = self.confirm(analysis, **fields)
            self.assertEqual(status, 200, response)
        final = self.analyses()[0]
        self.assertEqual(final["status"], "reviewed", final)
        self.assertEqual(final["material_source"], original_marker)
        self.assertNotEqual(final["decision_material_source"], original_marker)
        self.assertEqual([item["status"] for item in final["candidates"]], ["accepted", "accepted"])
        self.assertEqual(len(Provider.requests), 1)
        self.observe("new_analysis_own_decisions", reviewed=True, original_marker_preserved=True,
                     separate_confirmations=5, accepted_candidates=2, free_provider_calls=1)

    def test_stale_current_review_can_explain_deleted_material_without_provider(self):
        status, record = self.call("POST", "/api/records", {
            "type": "设定", "name": "可删除的合成资料", "description": "审稿曾消费的合成资料。",
        })
        self.assertEqual(status, 201, record)
        _, _, review = self.complete("semantic_review")
        self.assertEqual(self.call("DELETE", "/api/records/" + record["id"], {})[0], 200)
        stale = self.reviews()[0]
        self.assertTrue(stale.get("stale"), stale)
        self.assertIsInstance(stale["author_resolution_source"], dict)
        status, response = self.decide(review, resolution_source=stale["author_resolution_source"])
        self.assertEqual(status, 200, response)
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(len(Provider.requests), 1)
        self.observe("new_review_deleted_material", author_explanation_available=True,
                     gate_resolved=True, extra_provider_calls=0)

    def late_change(self, unknown_billing):
        job = self.queue()
        body = self.body()
        original = server.generate_ai

        def change_after_generate(*args, **kwargs):
            result = original(*args, **kwargs)
            if unknown_billing:
                result.update({"usage": None, "usage_state": "unknown", "billing_state": "unknown"})
            self.change(description="生成完成之后的来源变更，原候选必须保留。")
            return result

        with patch("server.generate_ai", side_effect=change_after_generate):
            completed = self.process(job)
        self.assertEqual(completed["status"], "failed", completed)
        result = completed["result"]
        self.assertEqual(result["completion_state"], "invalid_evidence")
        self.assertEqual(result["text"], "雨水顺着窗框淌下，她松开抵着铜扣的手。")
        self.assertFalse(result["safe_to_apply"])
        self.assertTrue(completed["materials_read_only"])
        expected_billing = "unknown" if unknown_billing else "usage_reported"
        self.assertEqual(result["billing_state"], expected_billing)
        with server.connect() as conn:
            quota = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                 (job["ai_run_id"],)).fetchone()[0]
            token = json.loads(conn.execute("SELECT token_json FROM ai_run WHERE id=?",
                                           (job["ai_run_id"],)).fetchone()[0])
            consent = json.loads(conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                             (job["ai_run_id"],)).fetchone()[0])
        self.assertEqual(quota, "unknown" if unknown_billing else "consumed")
        self.assertEqual(token["usage"], None if unknown_billing else {
            "prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110,
        })
        self.assertEqual(consent["billing_state"], expected_billing)
        calls, before = len(Provider.requests), self.ledger()
        statuses = []
        for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
            status, response = self.call("POST", f"/api/ai/{kind}/{identifier}/retry", {})
            statuses.append(status)
            self.assertEqual(status, 409, response)
        self.assertEqual(len(Provider.requests), calls)
        self.assertEqual(self.ledger(), before)
        self.assertEqual(self.body(), body)
        self.observe("late_material_change_unknown_billing" if unknown_billing else "late_material_change_known_usage",
                     status=completed["status"], completion_state=result["completion_state"],
                     complete_text_preserved=True, raw_usage_preserved=True, quota=quota,
                     retry_http_statuses=statuses, extra_provider_calls=0, body_unchanged=True)

    def test_late_material_change_retains_text_usage_and_blocks_both_retry_routes(self):
        self.late_change(False)

    def test_late_material_change_with_unknown_billing_cannot_resend(self):
        self.late_change(True)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def main():
    output = os.environ.get("MOYU_MATERIAL_LEGACY_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; choose a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    Provider.total_calls = 0
    MaterialLegacyHTTP.observations = []
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(MaterialLegacyHTTP))
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(MaterialLegacyHTTP.observations),
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "legacy_simulation": "normal local HTTP AI completion creates real job/run/report; remove only material seals from synthetic historical records; preserve request audit",
        "scope": "temporary synthetic SQLite; real loopback HTTP; free local provider; no real manuscript or credential",
        "claim_limit": "history/material integrity only; no literary-quality or first-tier competitiveness claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
