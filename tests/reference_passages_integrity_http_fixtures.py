#!/usr/bin/env python3
"""Frozen reference requests stay intact across dispatch, retry and adoption."""
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
from guidance_scope_change_http_fixtures import EvidenceResult
from reference_passages_http_fixtures import (
    ReferencePassagesHTTP, Provider, CANARY, prompt_material, fingerprints,
)
import moyu_materials
import server


def source_fingerprints():
    return {
        **fingerprints(),
        "tests/reference_passages_integrity_http_fixtures.py":
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


class ReferencePassagesIntegrityHTTP(ReferencePassagesHTTP):
    def write_context(self, job, context):
        with server.connect() as conn:
            conn.execute("UPDATE app_ai_job SET context_json=? WHERE id=?",
                         (server.json_text(context), job["id"]))

    def remove_both_reference_bindings(self, job):
        context, _, _ = self.assert_bound_passage(job)
        context.pop("reference_passages")
        context["_material_source"]["dependencies"] = [
            item for item in context["_material_source"]["dependencies"]
            if item["kind"] != "reference_section"
        ]
        self.write_context(job, context)

    def test_independent_legacy_author_resolution_ignores_all_reference_changes(self):
        with server.connect() as conn:
            before = moyu_materials.author_resolution_snapshot(conn, "default-project", "main")
        self.change_reference()
        self.change_reference(self.unconsumed, field="title")
        with server.connect() as conn:
            after = moyu_materials.author_resolution_snapshot(conn, "default-project", "main")
        self.assertEqual(before, after)
        self.assertEqual(before["purpose"], "author_resolution")

    def test_independent_unconsumed_reference_deletion_keeps_dispatch_valid(self):
        job = self.queue()
        _, consent, _ = self.assert_bound_passage(job)
        self.change_reference(self.unconsumed, remove=True)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success")
        self.assertEqual(Provider.requests, [consent["request_audit"]["payload"]])

    def test_independent_consumed_document_metadata_change_blocks_dispatch(self):
        job = self.queue()
        with server.connect() as conn:
            conn.execute("UPDATE app_reference_document SET metadata_json=? WHERE id=?",
                         (server.json_text({"synthetic_only": True, "revised": True}),
                          self.reference["document_id"]))
        self.assert_dispatch_blocked(job, "independent_document_metadata_change")

    def test_independent_duplicate_reference_dependency_blocks_dispatch(self):
        job = self.queue()
        context, _, _ = self.assert_bound_passage(job)
        dependencies = context["_material_source"]["dependencies"]
        dependencies.append(copy.deepcopy(next(item for item in dependencies
                                               if item["kind"] == "reference_section")))
        self.write_context(job, context)
        self.assert_dispatch_blocked(job, "independent_duplicate_reference_dependency")

    def test_independent_empty_or_null_passage_with_binding_blocks_dispatch(self):
        for value in (None, []):
            with self.subTest(value=value):
                job = self.queue()
                context, _, _ = self.assert_bound_passage(job)
                context["reference_passages"] = value
                self.write_context(job, context)
                self.assert_dispatch_blocked(job, "independent_null_or_empty_reference")

    def test_independent_wrong_binding_scope_blocks_dispatch(self):
        job = self.queue()
        context, _, _ = self.assert_bound_passage(job)
        context["_material_source"]["project_id"] = "synthetic-unrelated-project"
        self.write_context(job, context)
        self.assert_dispatch_blocked(job, "independent_wrong_marker_scope")

    def test_independent_reference_budget_rejection_leaves_all_tables_unchanged(self):
        reference = self.seed_reference("independent900", body="风" * 900)
        request = {**self.request, "reference_selections": [self.select(reference)],
                   "retrieval_profile": {**self.request["retrieval_profile"], "max_tokens": 6000}}
        self.assert_rejected_without_writes(request, "independent_budget_failure")

    def test_independent_analysis_without_selection_has_no_reference_payload(self):
        request = {key: value for key, value in self.request.items() if key != "reference_selections"}
        request.update(task="chapter_analysis", response_format="json")
        preview = self.preview(request)
        material = prompt_material(preview["request"])
        self.assertNotIn("reference_passages", material)
        self.assertNotIn(CANARY, preview["user"])

    def test_independent_changed_client_selection_after_queue_does_not_replace_frozen_quote(self):
        job = self.queue()
        _, consent, _ = self.assert_bound_passage(job)
        self.request["reference_selections"] = [self.select(self.unconsumed)]
        completed = self.process(job)
        self.assertEqual(completed["status"], "success")
        self.assertEqual(Provider.requests, [consent["request_audit"]["payload"]])

    def test_independent_lost_both_passage_and_dependency_cannot_dispatch_old_payload(self):
        job = self.queue()
        self.remove_both_reference_bindings(job)
        self.change_reference(remove=True)
        self.assert_dispatch_blocked(job, "independent_lost_both_reference_bindings")

    def test_independent_lost_both_bindings_blocks_job_and_run_retry(self):
        job = self.queue()
        self.assert_bound_passage(job)
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.remove_both_reference_bindings(job)
        self.change_reference(remove=True)
        self.assert_retries_blocked(job, "independent_lost_both_bindings_retry")

    def test_independent_lost_both_bindings_blocks_candidate_revision_and_apply(self):
        job = self.queue()
        self.assert_bound_passage(job)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success")
        self.remove_both_reference_bindings(job)
        self.change_reference(remove=True)
        before, body, calls = self.snapshot(), self.body(), len(Provider.requests)
        for operation in ("revise", "apply"):
            with self.subTest(operation=operation):
                if operation == "revise":
                    status, _ = self.revise(job, completed, request_id="independent-both-lost-revise")
                else:
                    status, _ = self.apply(job, completed)
                self.assertEqual(status, 400)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.body(), body)
                self.assertEqual(len(Provider.requests), calls)

    def test_independent_frozen_payload_quote_change_cannot_bypass_bound_context(self):
        job = self.queue()
        context, consent, _ = self.assert_bound_passage(job)
        material = prompt_material(consent["request_audit"]["payload"])
        material["reference_passages"][0]["source_quote"] = "伪造合成原句，不应发送。"
        user = next(item for item in consent["request_audit"]["payload"]["messages"]
                    if item["role"] == "user")
        _, separator, tail = user["content"].partition("\n</manuscript>")
        self.assertTrue(separator)
        user["content"] = "<manuscript>\n" + json.dumps(material, ensure_ascii=False) + separator + tail
        with server.connect() as conn:
            conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?",
                         (server.json_text(consent), job["ai_run_id"]))
        self.assert_dispatch_blocked(job, "independent_frozen_payload_quote_changed")


def main():
    output = os.environ.get("MOYU_REFERENCE_INTEGRITY_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path.")
    before = source_fingerprints()
    started = datetime.now(timezone.utc).isoformat()
    Provider.total_calls = 0
    ReferencePassagesIntegrityHTTP.observations = []
    names = [name for name in dir(ReferencePassagesIntegrityHTTP)
             if name.startswith("test_independent_")]
    suite = unittest.TestSuite(ReferencePassagesIntegrityHTTP(name) for name in names)
    with patch.dict(os.environ, {"MOYU_REFERENCE_PASSAGES_CAPTURE": ""}):
        result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = source_fingerprints()
    report = {
        "passed": result.wasSuccessful() and before == after,
        "checks": result.testsRun, "cases": result.cases,
        "observations": copy.deepcopy(ReferencePassagesIntegrityHTTP.observations),
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "production_database_accessed": False,
        "real_credentials_read": False, "external_model_calls": 0,
        "scope": "temporary synthetic SQLite; real loopback HTTP; free local provider",
        "claim_limit": "frozen reference integrity only; no literary-quality or competitiveness claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("cases", "observations", "source_files_sha256_before",
                                     "source_files_sha256_after")}, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
