#!/usr/bin/env python3
"""Saved chapter analyses bind author guidance without invalidating own decisions."""
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
from guidance_alias_http_fixtures import Provider, encode
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
    "tests/analysis_guidance_http_fixtures.py",
)


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


class AnalysisGuidanceHTTP(unittest.TestCase):
    observations = []
    process = legacy_fixture.MaterialLegacyHTTP.process
    preview = legacy_fixture.MaterialLegacyHTTP.preview
    body = legacy_fixture.MaterialLegacyHTTP.body
    state = legacy_fixture.MaterialLegacyHTTP.state
    queue = legacy_fixture.MaterialLegacyHTTP.queue
    analyses = legacy_fixture.MaterialLegacyHTTP.analyses
    confirm = legacy_fixture.MaterialLegacyHTTP.confirm

    def setUp(self):
        self.exchanges = []
        legacy_fixture.MaterialLegacyHTTP.setUp(self)

    def tearDown(self):
        try:
            capture = os.environ.get("MOYU_ANALYSIS_GUIDANCE_CAPTURE")
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

    def structured(self):
        result = legacy_fixture.MaterialLegacyHTTP.structured(self, "chapter_analysis")
        # New subjects exercise guidance changes caused by the analysis itself.
        for candidate, name in zip(result["candidates"], ("合成来客", "合成信使")):
            candidate["subject_name"] = name
        return result

    def complete(self):
        structured = self.structured()
        job = self.queue("chapter_analysis")

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
        with server.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM ai_run WHERE id=?", (job["ai_run_id"],),
            ).fetchone()[0], "success")
            self.assertEqual(conn.execute(
                "SELECT status FROM app_ai_job WHERE id=?", (job["id"],),
            ).fetchone()[0], "success")
        return job, completed["result"]["analysis"]

    def frozen(self, job):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT j.context_json,r.consent_json FROM app_ai_job j "
                "JOIN ai_run r ON r.id=j.ai_run_id WHERE j.id=?", (job["id"],),
            ).fetchone()
        return json.loads(row["context_json"]), json.loads(row["consent_json"])["request_audit"]

    def stored(self, analysis):
        with server.connect() as conn:
            return json.loads(conn.execute(
                "SELECT payload_json FROM app_chapter_analysis WHERE id=?", (analysis["id"],),
            ).fetchone()[0])

    def current(self, analysis):
        return next(item for item in self.analyses() if item["id"] == analysis["id"])

    def guidance(self):
        with server.connect() as conn:
            return moyu_workspace.guidance_source(conn, PROJECT, "main", self.chapter_id)

    def database_hash(self):
        with server.connect() as conn:
            return hashlib.sha256("\n".join(conn.iterdump()).encode("utf-8")).hexdigest()

    def counts(self):
        with server.connect() as conn:
            return {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                    for table in ("app_ai_job", "ai_run", "app_chapter_analysis", "candidate_memory",
                                  "assertion", "character_state", "source_span", "entity",
                                  "document_revision", "event_log")}

    def only_blueprint_change(self, field):
        before_chapters = self.state()["chapters"]
        status, before = self.call("GET", "/api/blueprint")
        self.assertEqual(status, 200, before)
        blueprint = copy.deepcopy(before["blueprint"])
        blueprint[field] = {
            "premise": "合成新前提：窗前等待改为明确拒绝来客。",
            "contract": "合成新创作约定：保留窗前线索，明确拒绝提前揭示来客。",
        }[field]
        materialize_calls = sum(row["route"] == "/api/blueprint/materialize" for row in self.exchanges)
        status, after = self.call("PUT", "/api/blueprint", {
            "expected_version": before["version"], "blueprint": blueprint,
        })
        self.assertEqual(status, 200, after)
        self.assertEqual(self.state()["chapters"], before_chapters)
        self.assertEqual(sum(row["route"] == "/api/blueprint/materialize" for row in self.exchanges),
                         materialize_calls)
        self.assertEqual(after["version"], before["version"] + 1)
        self.assertNotEqual(after["blueprint"][field], before["blueprint"][field])
        self.assertEqual({key: value for key, value in before["blueprint"].items() if key != field},
                         {key: value for key, value in after["blueprint"].items() if key != field})

    def original_sources_preserved(self, job, analysis, frozen):
        stored = self.stored(analysis)
        return {
            "frozen_context_unchanged": self.frozen(job)[0] == frozen[0],
            "request_audit_unchanged": self.frozen(job)[1] == frozen[1],
            "original_material_source_unchanged": stored.get("material_source") == analysis.get("material_source"),
            "original_guidance_source_unchanged": stored.get("guidance_source") == analysis.get("guidance_source"),
        }

    def assert_blueprint_blocks(self, field, action):
        job, analysis = self.complete()
        frozen = self.frozen(job)
        guidance = self.guidance()
        self.only_blueprint_change(field)
        status, history = self.call("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, history)
        current = self.current(analysis)
        before_state, before_database, before_counts = self.state(), self.database_hash(), self.counts()
        calls = len(Provider.requests)
        fields = {"confirm_" + action: True} if action != "candidates" else {
            "decisions": [{"candidate_id": candidate["id"], "decision": "accepted"}
                          for candidate in analysis["candidates"]],
        }
        confirm_status, response = self.confirm(analysis, **fields)
        preservation = self.original_sources_preserved(job, analysis, frozen)
        self.observe("blueprint_only_blocks_analysis", field=field, action=action,
                     actual_saved_job_and_run=True, guidance_changed=self.guidance() != guidance,
                     chapter_body_revision_and_plan_unchanged=True, extra_materialize_calls=0,
                     history_materials_read_only=history["materials_read_only"],
                     analysis_status=current["status"], analysis_stale=bool(current.get("stale")),
                     confirm_http_status=confirm_status,
                     state_unchanged=self.state() == before_state,
                     database_unchanged=self.database_hash() == before_database,
                     ledger_unchanged=self.counts() == before_counts,
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertEqual(current["status"], "stale", current)
        self.assertTrue(current.get("stale"), current)
        self.assertEqual(confirm_status, 409, response)
        self.assertTrue(history["materials_read_only"])
        self.assertEqual(self.state(), before_state)
        self.assertEqual(self.database_hash(), before_database)
        self.assertEqual(self.counts(), before_counts)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def test_premise_only_blocks_summary_without_writes(self):
        self.assert_blueprint_blocks("premise", "summary")

    def test_premise_only_blocks_handoff_without_writes(self):
        self.assert_blueprint_blocks("premise", "handoff")

    def test_premise_only_blocks_candidates_without_writes(self):
        self.assert_blueprint_blocks("premise", "candidates")

    def test_contract_only_blocks_summary_without_writes(self):
        self.assert_blueprint_blocks("contract", "summary")

    def test_contract_only_blocks_handoff_without_writes(self):
        self.assert_blueprint_blocks("contract", "handoff")

    def test_contract_only_blocks_candidates_without_writes(self):
        self.assert_blueprint_blocks("contract", "candidates")

    def test_current_analysis_sequential_decisions_are_current_and_idempotent(self):
        job, analysis = self.complete()
        frozen = self.frozen(job)
        before_guidance, before_counts = self.guidance(), self.counts()
        calls = len(Provider.requests)
        outcomes = []
        operations = [{"confirm_summary": True}, {"confirm_summary": True},
                      {"confirm_handoff": True}, {"confirm_handoff": True}]
        for candidate in analysis["candidates"]:
            operation = {"decisions": [{"candidate_id": candidate["id"], "decision": "accepted"}]}
            operations.extend((operation, copy.deepcopy(operation)))
        for fields in operations:
            status, response = self.confirm(analysis, **fields)
            outcomes.append({"http_status": status, "analysis_status": response.get("status"),
                             "listed_status": self.current(analysis)["status"]})
        current, stored = self.current(analysis), self.stored(analysis)
        chapter = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        after_counts = self.counts()
        preservation = self.original_sources_preserved(job, analysis, frozen)
        self.observe("current_sequential_decisions", outcomes=outcomes, analysis_status=current["status"],
                     analysis_stale=bool(current.get("stale")),
                     summary_saved=chapter["recap"] == analysis["summary"]["text"],
                     handoff_saved=chapter["next_brief"] == analysis["handoff"]["text"],
                     new_entities=after_counts["entity"] - before_counts["entity"],
                     new_memories=after_counts["candidate_memory"] - before_counts["candidate_memory"],
                     new_assertions=after_counts["assertion"] - before_counts["assertion"],
                     guidance_changed_by_own_decisions=self.guidance() != before_guidance,
                     decision_guidance_matches_current=stored.get("decision_guidance_source") == self.guidance(),
                     original_guidance_matches_frozen=stored.get("guidance_source") == frozen[0]["_guidance_source"],
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertEqual([row["http_status"] for row in outcomes], [200] * len(operations), outcomes)
        self.assertTrue(all(row["listed_status"] != "stale" for row in outcomes), outcomes)
        self.assertEqual(current["status"], "reviewed", current)
        self.assertFalse(current.get("stale"), current)
        self.assertEqual(chapter["recap"], analysis["summary"]["text"])
        self.assertEqual(chapter["next_brief"], analysis["handoff"]["text"])
        self.assertEqual(after_counts["entity"] - before_counts["entity"], 2)
        self.assertEqual(after_counts["candidate_memory"] - before_counts["candidate_memory"], 2)
        self.assertEqual(after_counts["assertion"] - before_counts["assertion"], 2)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(stored.get("guidance_source"), frozen[0]["_guidance_source"])
        self.assertEqual(stored.get("decision_guidance_source"), self.guidance())
        self.assertEqual(len(Provider.requests), calls)

    def test_external_guidance_change_after_own_handoff_blocks_remaining_candidates(self):
        job, analysis = self.complete()
        frozen = self.frozen(job)
        for fields in ({"confirm_summary": True}, {"confirm_handoff": True}):
            status, response = self.confirm(analysis, **fields)
            self.assertEqual(status, 200, response)
        before = self.stored(analysis)
        self.only_blueprint_change("contract")
        current = self.current(analysis)
        state, database, counts, calls = self.state(), self.database_hash(), self.counts(), len(Provider.requests)
        status, response = self.confirm(analysis, decisions=[
            {"candidate_id": item["id"], "decision": "accepted"} for item in analysis["candidates"]
        ])
        preservation = self.original_sources_preserved(job, analysis, frozen)
        self.observe("guidance_change_after_own_handoff", analysis_status=current["status"],
                     confirm_http_status=status, state_unchanged=self.state() == state,
                     database_unchanged=self.database_hash() == database,
                     ledger_unchanged=self.counts() == counts,
                     decision_guidance_source_unchanged=self.stored(analysis).get("decision_guidance_source")
                     == before.get("decision_guidance_source"),
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertEqual(current["status"], "stale", current)
        self.assertEqual(status, 409, response)
        self.assertEqual(self.database_hash(), database)
        self.assertEqual(self.state(), state)
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.stored(analysis).get("decision_guidance_source"), before.get("decision_guidance_source"))
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)

    def test_same_manuscript_with_new_guidance_has_distinct_analysis_identity(self):
        first_job, first = self.complete()
        first_frozen = self.frozen(first_job)
        chapter = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.only_blueprint_change("premise")
        second_job, second = self.complete()
        second_frozen = self.frozen(second_job)
        first_current, second_current = self.current(first), self.current(second)
        self.observe("analysis_guidance_identity", actual_saved_jobs_and_runs=2,
                     same_manuscript_revision=first["source_revision_id"] == second["source_revision_id"],
                     same_manuscript_hash=first["chapter_hash"] == second["chapter_hash"],
                     different_frozen_guidance=first_frozen[0]["_guidance_source"] != second_frozen[0]["_guidance_source"],
                     same_frozen_materials=first_frozen[0]["_material_source"] == second_frozen[0]["_material_source"],
                     distinct_analysis_id=first["id"] != second["id"],
                     distinct_analysis_key=first["analysis_key"] != second["analysis_key"],
                     second_analysis_bound_to_new_run=second["run_id"] == second_job["ai_run_id"],
                     first_status=first_current["status"], second_status=second_current["status"],
                     saved_analysis_count=self.counts()["app_chapter_analysis"],
                     first_frozen_request_unchanged=self.frozen(first_job) == first_frozen,
                     chapter_unchanged=chapter == next(item for item in self.state()["chapters"]
                                                       if item["id"] == self.chapter_id),
                     free_provider_calls=len(Provider.requests))
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["analysis_key"], second["analysis_key"])
        self.assertEqual(second["run_id"], second_job["ai_run_id"])
        self.assertEqual(first_current["status"], "stale", first_current)
        self.assertEqual(second_current["status"], "pending", second_current)
        self.assertEqual(self.counts()["app_chapter_analysis"], 2)
        self.assertEqual(first_frozen[0]["_material_source"], second_frozen[0]["_material_source"])
        self.assertEqual(self.frozen(first_job), first_frozen)
        self.assertEqual(len(Provider.requests), 2)

    def test_real_saved_ai_analysis_missing_guidance_is_read_only(self):
        job, analysis = self.complete()
        frozen = self.frozen(job)
        with server.connect() as conn:
            legacy = self.stored(analysis)
            legacy.pop("guidance_source", None)
            legacy.pop("decision_guidance_source", None)
            conn.execute("UPDATE app_chapter_analysis SET payload_json=? WHERE id=?",
                         (server.json_text(legacy), analysis["id"]))
        current = self.current(legacy)
        state, database, counts, calls = self.state(), self.database_hash(), self.counts(), len(Provider.requests)
        outcomes = []
        for fields in ({"confirm_summary": True}, {"confirm_handoff": True}, {
            "decisions": [{"candidate_id": item["id"], "decision": "accepted"} for item in legacy["candidates"]],
        }):
            status, response = self.confirm(legacy, **fields)
            outcomes.append(status)
        self.observe("legacy_guidance_missing", actual_saved_job_and_run=True,
                     original_material_source_preserved=legacy["material_source"] == frozen[0]["_material_source"],
                     missing_guidance_source=True, analysis_status=current["status"],
                     confirm_http_statuses=outcomes, state_unchanged=self.state() == state,
                     database_unchanged=self.database_hash() == database,
                     ledger_unchanged=self.counts() == counts,
                     frozen_request_unchanged=self.frozen(job) == frozen,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(current["status"], "stale", current)
        self.assertEqual(outcomes, [409, 409, 409], response)
        self.assertEqual(self.state(), state)
        self.assertEqual(self.database_hash(), database)
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.frozen(job), frozen)
        self.assertEqual(len(Provider.requests), calls)

    def test_unlinked_component_analysis_without_frozen_sources_remains_compatible(self):
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                chapter = next(item for item in server.chapter_rows(conn) if item["id"] == self.chapter_id)
                analysis = moyu_analysis.save_analysis(conn, {
                    "project_id": PROJECT, "branch_id": "main", "actor_id": "fixture-author",
                    "records": server.state_from_db(conn)["records"],
                }, chapter, "component-only-unlinked-run", self.structured())
        outcomes = []
        for fields in ({"confirm_summary": True}, {"confirm_handoff": True}, {
            "decisions": [{"candidate_id": item["id"], "decision": "accepted"} for item in analysis["candidates"]],
        }):
            status, response = self.confirm(analysis, **fields)
            outcomes.append(status)
        current = self.current(analysis)
        self.observe("component_compatibility", linked_ai_run_count=self.counts()["ai_run"],
                     confirm_http_statuses=outcomes, analysis_status=current["status"],
                     no_guidance_marker="guidance_source" not in analysis,
                     no_material_marker="material_source" not in analysis,
                     free_provider_calls=len(Provider.requests))
        self.assertEqual(outcomes, [200, 200, 200], response)
        self.assertEqual(current["status"], "reviewed", current)
        self.assertEqual(self.counts()["ai_run"], 0)
        self.assertNotIn("guidance_source", analysis)
        self.assertNotIn("material_source", analysis)
        self.assertEqual(len(Provider.requests), 0)

    def test_late_analysis_save_rechecks_original_guidance_without_writes(self):
        job, analysis = self.complete()
        frozen = self.frozen(job)
        chapter = next(item for item in self.state()["chapters"] if item["id"] == self.chapter_id)
        self.only_blueprint_change("premise")
        state, database, counts, calls = self.state(), self.database_hash(), self.counts(), len(Provider.requests)
        rejected = False
        with moyu_workspace.use_scope(PROJECT, "main", "fixture-author"):
            with server.connect() as conn:
                try:
                    moyu_analysis.save_analysis(conn, {
                        "project_id": PROJECT, "branch_id": "main", "actor_id": "fixture-author",
                        "records": server.state_from_db(conn)["records"], "sent_context": frozen[0],
                    }, chapter, job["ai_run_id"], self.structured())
                except moyu_analysis.AnalysisConflict:
                    rejected = True
        preservation = self.original_sources_preserved(job, analysis, frozen)
        self.observe("late_frozen_guidance_save", rejected=rejected,
                     actual_saved_job_and_run=True, state_unchanged=self.state() == state,
                     database_unchanged=self.database_hash() == database,
                     ledger_unchanged=self.counts() == counts,
                     extra_provider_calls=len(Provider.requests) - calls, **preservation)
        self.assertTrue(rejected)
        self.assertEqual(self.state(), state)
        self.assertEqual(self.database_hash(), database)
        self.assertEqual(self.counts(), counts)
        self.assertTrue(all(preservation.values()), preservation)
        self.assertEqual(len(Provider.requests), calls)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def freeze_sources(phase, expected):
    capture = os.environ.get("MOYU_ANALYSIS_GUIDANCE_CAPTURE")
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


def safe_report(value):
    if isinstance(value, str):
        return value.replace(str(ROOT), ".").replace(str(Path.home()), "<private-home>")
    if isinstance(value, list):
        return [safe_report(item) for item in value]
    if isinstance(value, dict):
        return {key: safe_report(item) for key, item in value.items()}
    return value


def main():
    output = os.environ.get("MOYU_ANALYSIS_GUIDANCE_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    before_capture = freeze_sources("before", before)
    Provider.total_calls = 0
    AnalysisGuidanceHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisGuidanceHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    after_capture = freeze_sources("after", after)
    report = safe_report({
        "passed": result.wasSuccessful() and before == after,
        "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(AnalysisGuidanceHTTP.observations),
        "synthetic_only": True, "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "frozen_source_before": before_capture, "frozen_source_after": after_capture,
        "source_unchanged": before == after,
        "scope": "real saved AI jobs and runs in temporary synthetic SQLite; loopback HTTP; free local provider",
        "claim_limit": "chapter analysis author-guidance integrity only; no real manuscript, credential, literary-quality or first-tier claim",
    })
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
