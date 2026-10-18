#!/usr/bin/env python3
"""Author explanations survive archival status without weakening model sources."""
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

import material_legacy_http_fixtures as legacy_fixture
from guidance_alias_http_fixtures import Provider
from guidance_scope_change_http_fixtures import EvidenceResult
from backend import moyu_materials
from backend import server


PROJECT = "default-project"
REASON = "作者已结合当前合成稿件核对，这处叙事安排可以保留。"
SOURCES = (
    "backend/server.py", "backend/moyu_analysis.py", "backend/moyu_materials.py", "backend/moyu_context.py",
    "backend/moyu_workspace.py", "backend/moyu_records.py", "frontend/author-workspace.js", "frontend/app.js",
    "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py",
    "tests/material_legacy_http_fixtures.py",
    "tests/author_resolution_material_http_fixtures.py",
)


class AuthorResolutionMaterialHTTP(unittest.TestCase):
    observations = []
    process = legacy_fixture.MaterialLegacyHTTP.process
    preview = legacy_fixture.MaterialLegacyHTTP.preview
    body = legacy_fixture.MaterialLegacyHTTP.body
    state = legacy_fixture.MaterialLegacyHTTP.state
    change = legacy_fixture.MaterialLegacyHTTP.change
    ledger = legacy_fixture.MaterialLegacyHTTP.ledger
    queue = legacy_fixture.MaterialLegacyHTTP.queue
    structured = legacy_fixture.MaterialLegacyHTTP.structured
    complete = legacy_fixture.MaterialLegacyHTTP.complete
    legacy = legacy_fixture.MaterialLegacyHTTP.legacy
    reviews = legacy_fixture.MaterialLegacyHTTP.reviews
    decide = legacy_fixture.MaterialLegacyHTTP.decide
    gate = legacy_fixture.MaterialLegacyHTTP.gate

    def setUp(self):
        self.exchanges = []
        legacy_fixture.MaterialLegacyHTTP.setUp(self)

    def tearDown(self):
        capture = os.environ.get("MOYU_AUTHOR_RESOLUTION_CAPTURE")
        if capture:
            target = Path(capture) / self._testMethodName
            target.mkdir(parents=True, exist_ok=False)
            with server.connect() as source, sqlite3.connect(target / "synthetic-book.sqlite3") as dest:
                source.backup(dest)
            (target / "http-and-provider.json").write_text(json.dumps({
                "synthetic_only": True, "paid_provider_calls": 0,
                "http": self.exchanges, "provider_requests": Provider.requests,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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

    def chapter_change(self, **fields):
        status, chapter = self.call("PATCH", "/api/chapters/" + self.chapter_id, fields)
        self.assertEqual(status, 200, chapter)
        return chapter

    def frozen(self, job):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT j.context_json,r.consent_json FROM app_ai_job j "
                "JOIN ai_run r ON r.id=j.ai_run_id WHERE j.id=?", (job["id"],),
            ).fetchone()
        return json.loads(row["context_json"]), json.loads(row["consent_json"])

    def current_report(self, review):
        return next(item for item in self.reviews() if item["id"] == review["id"])

    def resolve(self, review, decision="explained"):
        current = self.current_report(review)
        fields = {"reason": REASON}
        if current.get("stale"):
            fields["resolution_source"] = current["author_resolution_source"]
        calls = len(Provider.requests)
        status, response = self.decide(review, decision, **fields)
        self.assertEqual(status, 200, response)
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(len(Provider.requests), calls)
        return response["review"]

    def archive(self, review, decision):
        calls = len(Provider.requests)
        before = self.current_report(review)
        before_source = copy.deepcopy(before["author_resolution_source"])
        outcomes = []
        for stage in ("prewrite", "precommit", "postcommit"):
            if stage == "precommit":
                self.chapter_change(status="完成")
            status, committed = self.call("POST", "/api/chapters/" + self.chapter_id + "/commit", {
                "stage": stage,
            })
            outcomes.append({
                "stage": stage, "http_status": status, "status": committed.get("status"),
                "blocking": committed.get("review", {}).get("blocking", []),
            })
        after = self.current_report(review)
        self.observe("author_explanation_archival", decision=decision, commits=outcomes,
                     source_unchanged=after["author_resolution_source"] == before_source,
                     explanation_current=after["findings"][0]["author_resolution_current"],
                     original_model_review_stale=bool(after.get("stale")),
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual([item["http_status"] for item in outcomes], [201, 201, 201], outcomes)
        self.assertEqual([item["status"] for item in outcomes], ["passed", "passed", "passed"], outcomes)
        self.assertEqual(after["author_resolution_source"], before_source)
        self.assertTrue(after["findings"][0]["author_resolution_current"])
        self.assertTrue(after.get("stale"), after)
        self.assertEqual(self.gate()["issues"], [])
        self.assertEqual(len(Provider.requests), calls)
        return after

    def test_current_explained_survives_status_change_and_all_archival_stages(self):
        job, _, review = self.complete("semantic_review")
        context, _ = self.frozen(job)
        self.resolve(review)
        after = self.archive(review, "explained")
        self.assertEqual(after["findings"][0]["decision"], "explained")
        self.assertEqual(after["material_source"], context["_material_source"])
        self.assertEqual(len(Provider.requests), 1)

    def test_current_ignored_survives_status_change_and_all_archival_stages(self):
        _, _, review = self.complete("semantic_review")
        self.resolve(review, "ignored")
        after = self.archive(review, "ignored")
        self.assertEqual(after["findings"][0]["decision"], "ignored")
        self.assertEqual(len(Provider.requests), 1)

    def test_rewritten_stale_review_can_explain_current_draft_then_archive(self):
        _, _, review = self.complete("semantic_review")
        self.assertEqual(self.decide(review, "accepted", reason="作者先检查这处问题。")[0], 200)
        self.chapter_change(body=self.body() + "\n来客把铜扣留在窗台，转身走入雨幕。")
        stale = self.current_report(review)
        self.assertTrue(stale.get("stale"), stale)
        self.assertEqual(stale["findings"][0]["repair_targets"], [])
        self.resolve(review)
        after = self.archive(review, "stale_current_draft_explained")
        self.assertEqual(after["findings"][0]["decision"], "accepted")
        self.assertEqual(after["findings"][0]["stale_resolution"]["decision"], "explained")
        self.assertEqual(len(Provider.requests), 1)

    def test_real_legacy_missing_markers_can_explain_current_draft_then_archive(self):
        job, _, review = self.legacy("semantic_review", without_guidance=True)
        stale = self.current_report(review)
        self.assertTrue(stale.get("stale"), stale)
        self.assertIn("legacy_material_source", stale["author_resolution_source"])
        self.assertNotIn("material_source", stale)
        self.resolve(review)
        after = self.archive(review, "legacy_current_snapshot_explained")
        status, history = self.call("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, history)
        self.assertTrue(history["materials_read_only"])
        self.assertTrue(after["findings"][0]["author_resolution_current"])
        self.assertEqual(len(Provider.requests), 1)

    def assert_change_invalidates_explanation(self, kind):
        _, _, review = self.complete("semantic_review")
        self.resolve(review)
        baseline = self.current_report(review)
        source = copy.deepcopy(baseline["author_resolution_source"])
        self.chapter_change(status="完成")
        after_status = self.current_report(review)
        if kind == "body":
            self.chapter_change(body=self.body() + "\n作者再次修改铜扣的去向。")
        elif kind == "record":
            self.change(description="作者解释之后，人物的当前设定已改为另一项。")
        else:
            self.chapter_change(scene_plan="作者解释之后，改为在雨幕外重新检查来客。")
        changed = self.current_report(review)
        before_log = copy.deepcopy(changed["decision_log"])
        status, response = self.decide(review, resolution_source=source, reason=REASON)
        codes = [issue["code"] for issue in self.gate()["issues"]]
        self.observe("author_explanation_material_change", kind=kind,
                     status_only_explanation_current=after_status["findings"][0]["author_resolution_current"],
                     source_changed=changed["author_resolution_source"] != source,
                     changed_explanation_current=changed["findings"][0]["author_resolution_current"],
                     stale_resolution_http_status=status, gate_codes=codes,
                     extra_provider_calls=len(Provider.requests) - 1)
        self.assertTrue(changed.get("stale"), changed)
        self.assertFalse(changed["findings"][0]["author_resolution_current"])
        self.assertNotEqual(changed["author_resolution_source"], source)
        self.assertEqual(status, 409, response)
        self.assertEqual(self.current_report(review)["decision_log"], before_log)
        self.assertIn("semantic_review_stale", codes)
        self.assertEqual(len(Provider.requests), 1)
        self.assertTrue(after_status["findings"][0]["author_resolution_current"], after_status)

    def test_body_change_still_expires_explanation_after_status_transition(self):
        self.assert_change_invalidates_explanation("body")

    def test_record_change_still_expires_explanation_after_status_transition(self):
        self.assert_change_invalidates_explanation("record")

    def test_plan_change_still_expires_explanation_after_status_transition(self):
        self.assert_change_invalidates_explanation("plan")

    def test_author_resolution_purpose_marker_cannot_authorize_model_materials(self):
        job, _, review = self.complete("semantic_review")
        self.chapter_change(body=self.body() + "\n作者补充了当下稿件的动作。")
        self.resolve(review)
        source = self.current_report(review)["author_resolution_source"]
        marker = source["material_source"]
        self.observe("author_marker_purpose", purpose=marker.get("purpose"),
                     real_saved_run_id=job["ai_run_id"])
        self.assertEqual(marker.get("purpose"), "author_resolution", marker)
        context, _ = self.frozen(job)
        strict = context["_material_source"]
        self.assertNotIn("purpose", strict)
        calls, before_ledger = len(Provider.requests), self.ledger()
        rejected = False
        with server.connect() as conn:
            try:
                moyu_materials.validate(conn, PROJECT, "main", marker)
            except ValueError:
                rejected = True
        self.observe("author_marker_strict_validation", rejected=rejected,
                     ledger_unchanged=self.ledger() == before_ledger,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertTrue(rejected)
        self.assertEqual(self.ledger(), before_ledger)
        self.assertEqual(len(Provider.requests), calls)

    def test_status_change_keeps_old_queued_and_completed_model_tasks_strict(self):
        queued = self.queue()
        completed_job = self.queue()
        completed = self.process(completed_job)
        self.assertEqual(completed["status"], "success", completed)
        frozen_queued, consent = self.frozen(queued)
        frozen_completed, _ = self.frozen(completed_job)
        self.chapter_change(status="完成")
        body, calls = self.body(), len(Provider.requests)
        dispatched = self.process(queued)
        self.assertEqual(dispatched["status"], "failed", dispatched)
        self.assertEqual(dispatched["result"]["billing_state"], "not_dispatched")
        current_context, current_consent = self.frozen(queued)
        self.assertEqual(current_context, frozen_queued)
        self.assertEqual(current_consent["request_audit"], consent["request_audit"])
        before = self.ledger()
        status, response = self.call("POST", "/api/ai/runs/" + completed_job["ai_run_id"] + "/apply", {
            **completed_job["source"], "decision": "accepted",
            "replacement": "\n\n" + completed["result"]["text"],
        })
        self.assertEqual(status, 400, response)
        self.assertEqual(self.ledger(), before)
        status, revised = self.call("POST", "/api/ai/runs/" + completed_job["ai_run_id"] + "/revise", {
            "request_id": "author-status-strict-revision",
            "candidate_hash": server.hash_text(completed["result"]["text"]),
            "notes": "保留严格模型来源校验。", "defer": True,
        })
        self.assertEqual(status, 400, revised)
        self.assertEqual(self.ledger(), before)
        with patch("backend.server.start_ai_worker", return_value=False):
            statuses = []
            for entry, identifier in (("jobs", queued["id"]), ("runs", queued["ai_run_id"])):
                retry_status, result = self.call("POST", f"/api/ai/{entry}/{identifier}/retry", {
                    "mode": "original",
                })
                statuses.append(retry_status)
                self.assertEqual(retry_status, 409, result)
        self.assertEqual(self.ledger(), before)
        self.assertEqual(self.frozen(completed_job)[0], frozen_completed)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), calls)
        self.observe("strict_model_status_sources", dispatch_status=dispatched["status"],
                     billing_state=dispatched["result"]["billing_state"], apply_http_status=status,
                     revise_http_status=400, retry_http_statuses=statuses,
                     queued_and_completed_contexts_preserved=True,
                     body_unchanged=True, extra_provider_calls=len(Provider.requests) - calls)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def freeze_sources(phase, expected):
    capture = os.environ.get("MOYU_AUTHOR_RESOLUTION_CAPTURE")
    if not capture:
        return None
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
    return str(target)


def main():
    output = os.environ.get("MOYU_AUTHOR_RESOLUTION_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    before_capture = freeze_sources("before", before)
    Provider.total_calls = 0
    AuthorResolutionMaterialHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AuthorResolutionMaterialHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    after_capture = freeze_sources("after", after)
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(AuthorResolutionMaterialHTTP.observations),
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "frozen_source_before": before_capture, "frozen_source_after": after_capture,
        "source_unchanged": before == after,
        "scope": "real saved AI jobs and runs in temporary synthetic SQLite; loopback HTTP; free local provider",
        "claim_limit": "author decision archival integrity only; no real manuscript, credential, literary-quality or first-tier claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
