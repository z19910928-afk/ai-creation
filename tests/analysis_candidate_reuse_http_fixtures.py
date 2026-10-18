#!/usr/bin/env python3
"""Repeated chapter analyses reuse unchanged canon without overwriting decisions."""
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
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import material_legacy_http_fixtures as legacy
from guidance_alias_http_fixtures import Provider, encode
from guidance_scope_change_http_fixtures import EvidenceResult
from backend import server


PROJECT = "default-project"
CANON_TABLES = (
    "candidate_memory", "assertion", "source_span", "entity", "character_state",
    "app_story_relation", "app_timeline_event", "app_plot_item", "app_foreshadow_link",
)
SOURCES = (
    "backend/server.py", "backend/moyu_analysis.py", "backend/moyu_context.py", "backend/moyu_materials.py",
    "backend/moyu_workspace.py", "backend/moyu_records.py", "backend/moyu_provider.py",
    "tests/guidance_alias_http_fixtures.py", "tests/guidance_scope_change_http_fixtures.py",
    "tests/material_legacy_http_fixtures.py", "tests/analysis_candidate_reuse_http_fixtures.py",
)


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def rows(conn, sql, parameters=()):
    return [
        {key: {"blob_sha256": hashlib.sha256(value).hexdigest()}
         if isinstance(value, bytes) else value for key, value in dict(row).items()}
        for row in conn.execute(sql, parameters)
    ]


def artifact_ids(candidate):
    return {key: value for key, value in candidate.get("artifacts", {}).items()
            if key.endswith("_id")}


class AnalysisCandidateReuseHTTP(unittest.TestCase):
    observations = []
    process = legacy.MaterialLegacyHTTP.process
    preview = legacy.MaterialLegacyHTTP.preview
    body = legacy.MaterialLegacyHTTP.body
    state = legacy.MaterialLegacyHTTP.state
    change = legacy.MaterialLegacyHTTP.change

    def setUp(self):
        self.exchanges = []
        legacy.MaterialLegacyHTTP.setUp(self)
        self.original_manuscript = self.manuscript()

    def tearDown(self):
        try:
            capture = os.environ.get("MOYU_ANALYSIS_CANDIDATE_REUSE_CAPTURE")
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
            legacy.MaterialLegacyHTTP.tearDown(self)

    def call(self, method, route, payload=None, project=PROJECT):
        status, response = legacy.MaterialLegacyHTTP.call(self, method, route, payload, project)
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

    def canonical(self, project=PROJECT):
        with server.connect() as conn:
            return {
                table: {"count": len(values), "rows_sha256": digest(values)}
                for table in CANON_TABLES
                for values in [rows(conn, "SELECT * FROM " + table +
                                    " WHERE project_id=? ORDER BY id", (project,))]
            }

    def manuscript(self, project=PROJECT):
        with server.connect() as conn:
            return digest(rows(
                conn, "SELECT r.* FROM document_revision r JOIN document_node n "
                "ON n.id=r.document_node_id WHERE n.project_id=? ORDER BY r.id", (project,),
            ))

    def raw(self):
        result = legacy.MaterialLegacyHTTP.structured(self, "chapter_analysis")
        result["candidates"] = result["candidates"][:1]
        return result

    def complete(self, structured, project=PROJECT, chapter_id=None):
        status, job = self.call("POST", "/api/ai/queue", {
            **self.request, "chapter_id": chapter_id or self.chapter_id,
            "task": "chapter_analysis", "response_format": "json", "defer": True,
        }, project)
        self.assertEqual(status, 202, job)

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
            status, completed = self.call("POST", "/api/ai/jobs/" + job["id"] + "/process", {}, project)
        self.assertEqual(status, 200, completed)
        self.assertEqual(completed["status"], "success", completed)
        analysis = completed["result"]["analysis"]
        self.assertEqual(analysis["run_id"], job["ai_run_id"], analysis)
        return analysis

    def confirm(self, analysis, *, edited=None, project=PROJECT):
        decision = {"candidate_id": analysis["candidates"][0]["id"], "decision": "accepted"}
        if edited is not None:
            decision["edited"] = edited
        return self.call("POST", "/api/chapter-analysis/" + analysis["id"] + "/confirm", {
            "source_revision_id": analysis["source_revision_id"],
            "chapter_hash": analysis["chapter_hash"], "decisions": [decision],
        }, project)

    def accept_first(self, raw):
        first = self.complete(raw)
        self.assertEqual(first["candidates"][0]["subject_name"], self.person["name"])
        self.assertFalse(first["candidates"][0].get("known_by_unresolved"))
        status, response = self.confirm(first)
        self.assertEqual(status, 200, response)
        candidate = response["analysis"]["candidates"][0]
        self.assertEqual(candidate["status"], "accepted")
        self.assertTrue({"memory_id", "assertion_id", "source_span_id", "timeline_id"} <=
                        set(artifact_ids(candidate)), candidate)
        return first, candidate

    def change_premise(self):
        status, before = self.call("GET", "/api/blueprint")
        self.assertEqual(status, 200, before)
        blueprint = copy.deepcopy(before["blueprint"])
        blueprint["premise"] = "合成前提已调整，正文与候选原句保持相同。"
        status, after = self.call("PUT", "/api/blueprint", {
            "expected_version": before["version"], "blueprint": blueprint,
        })
        self.assertEqual(status, 200, after)
        self.assertEqual(after["version"], before["version"] + 1)

    def assert_reuse(self, change_kind):
        raw = self.raw()
        first, accepted = self.accept_first(raw)
        if change_kind == "premise":
            self.change_premise()
        else:
            self.change(description="合成人物资料已修改，原句和候选事实不变。")
        second = self.complete(raw)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["candidates"][0]["id"], first["candidates"][0]["id"])
        before, calls = self.canonical(), len(Provider.requests)
        status, response = self.confirm(second)
        after = self.canonical()
        current = (response.get("analysis") or {}).get("candidates", [{}])[0]
        self.observe("same_candidate_after_" + change_kind,
                     new_analysis_identity=True, same_candidate_identity=True,
                     first_accept_http_status=200, second_accept_http_status=status,
                     second_accept_error=response.get("error"),
                     canonical_unchanged=before == after,
                     canonical_artifact_ids_reused=artifact_ids(current) == artifact_ids(accepted),
                     original_artifact_ids=artifact_ids(accepted),
                     current_artifact_ids=artifact_ids(current),
                     canonical_before=before, canonical_after=after,
                     manuscript_and_all_revisions_unchanged=self.manuscript() == self.original_manuscript,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(status, 200, response)
        self.assertEqual(artifact_ids(current), artifact_ids(accepted))
        self.assertEqual(after, before)
        self.assertEqual(self.manuscript(), self.original_manuscript)
        self.assertEqual(len(Provider.requests), calls)
        with server.connect() as conn:
            memory = conn.execute(
                "SELECT status FROM candidate_memory WHERE id=?", (accepted["artifacts"]["memory_id"],),
            ).fetchone()
            assertion = conn.execute(
                "SELECT state FROM assertion WHERE id=?", (accepted["artifacts"]["assertion_id"],),
            ).fetchone()
        self.assertEqual(memory["status"], "accepted")
        self.assertEqual(assertion["state"], "canon")

    def assert_rejected_atomically(self, analysis, scenario, *, edited=None):
        before, canonical, state = self.database_hash(), self.canonical(), self.state()
        calls = len(Provider.requests)
        status, response = self.confirm(analysis, edited=edited)
        error = str(response.get("error") or "")
        self.observe(scenario, confirm_http_status=status, error=error,
                     database_unchanged=self.database_hash() == before,
                     canonical_unchanged=self.canonical() == canonical,
                     state_unchanged=self.state() == state,
                     manuscript_and_all_revisions_unchanged=self.manuscript() == self.original_manuscript,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(status, 409, response)
        self.assertEqual(self.database_hash(), before)
        self.assertEqual(self.canonical(), canonical)
        self.assertEqual(self.state(), state)
        self.assertEqual(self.manuscript(), self.original_manuscript)
        self.assertEqual(len(Provider.requests), calls)
        self.assertNotIn("UNIQUE", error)
        self.assertRegex(error, r"[\u4e00-\u9fff]")
        self.assertRegex(error, r"请|重新|核对|修改|修订|审查")

    def test_same_raw_candidate_after_premise_change_reuses_canon(self):
        self.assert_reuse("premise")

    def test_same_raw_candidate_after_description_change_reuses_canon(self):
        self.assert_reuse("description")

    def test_changed_note_known_by_and_author_edit_are_not_blindly_reused(self):
        raw = self.raw()
        first, _ = self.accept_first(raw)
        for change_kind in ("note", "known_by", "author_edit"):
            with self.subTest(change=change_kind):
                self.change(description="合成资料版本：" + change_kind)
                changed = copy.deepcopy(raw)
                edited = None
                if change_kind == "note":
                    changed["candidates"][0]["note"] = "同一原句的结论已改，不能覆盖原正式记忆。"
                elif change_kind == "known_by":
                    changed["candidates"][0]["known_by"] = []
                else:
                    edited = {"note": "作者编辑过本次候选说明，需要明确修订旧记忆。"}
                second = self.complete(changed)
                self.assertNotEqual(second["id"], first["id"])
                self.assertEqual(second["candidates"][0]["id"], first["candidates"][0]["id"])
                self.assert_rejected_atomically(second, "changed_" + change_kind, edited=edited)

    def test_withdrawn_memory_cannot_be_reaccepted_from_a_new_report(self):
        raw = self.raw()
        first, accepted = self.accept_first(raw)
        status, response = self.call("PATCH", "/api/memory/" + accepted["artifacts"]["memory_id"], {
            "status": "rejected", "reason": "作者已撤回这条合成记忆。",
        })
        self.assertEqual(status, 200, response)
        self.change(description="合成资料已更新，旧记忆仍为撤回状态。")
        second = self.complete(raw)
        self.assertNotEqual(second["id"], first["id"])
        self.assert_rejected_atomically(second, "withdrawn_memory")
        with server.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM candidate_memory WHERE id=?", (accepted["artifacts"]["memory_id"],),
            ).fetchone()[0], "rejected")

    def test_invalid_assertion_cannot_be_restored_by_a_new_report(self):
        raw = self.raw()
        first, accepted = self.accept_first(raw)
        status, response = self.call(
            "POST", "/api/assertions/" + accepted["artifacts"]["assertion_id"] + "/review",
            {"state": "retracted", "reason": "作者已撤回这条合成正式事实。"},
        )
        self.assertEqual(status, 200, response)
        self.change(description="合成资料已更新，原正式事实仍已失效。")
        second = self.complete(raw)
        self.assertNotEqual(second["id"], first["id"])
        self.assert_rejected_atomically(second, "invalid_assertion")
        with server.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT state FROM assertion WHERE id=?", (accepted["artifacts"]["assertion_id"],),
            ).fetchone()[0], "retracted")

    def test_same_report_repeat_remains_idempotent(self):
        first, accepted = self.accept_first(self.raw())
        before, calls = self.canonical(), len(Provider.requests)
        status, response = self.confirm(first)
        current = (response.get("analysis") or {}).get("candidates", [{}])[0]
        self.observe("same_report_repeat", confirm_http_status=status,
                     canonical_unchanged=self.canonical() == before,
                     canonical_artifact_ids_reused=artifact_ids(current) == artifact_ids(accepted),
                     decision_count=len((response.get("analysis") or {}).get("decisions", [])),
                     manuscript_and_all_revisions_unchanged=self.manuscript() == self.original_manuscript,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(status, 200, response)
        self.assertEqual(self.canonical(), before)
        self.assertEqual(artifact_ids(current), artifact_ids(accepted))
        self.assertEqual(len(response["analysis"]["decisions"]), 1)
        self.assertEqual(self.manuscript(), self.original_manuscript)
        self.assertEqual(len(Provider.requests), calls)

    def test_same_prose_in_another_book_does_not_reuse_foreign_canon(self):
        raw = self.raw()
        first, accepted = self.accept_first(raw)
        before = self.canonical()
        status, result = self.call("POST", "/api/projects", {"title": "重复候选隔离合成作品"})
        self.assertEqual(status, 201, result)
        other_project = result["project"]["id"]
        status, state = self.call("GET", "/api/state", project=other_project)
        self.assertEqual(status, 200, state)
        other_chapter = state["chapters"][0]["id"]
        status, _ = self.call("PATCH", "/api/chapters/" + other_chapter, {
            "body": self.body(),
        }, other_project)
        self.assertEqual(status, 200)
        status, person = self.call("POST", "/api/records", {
            "type": "人物", "name": self.person["name"], "description": "另一作品的同名合成人物。",
        }, other_project)
        self.assertEqual(status, 201, person)
        status, _ = self.call("PUT", "/api/ai/settings", {
            "base_url": f"http://127.0.0.1:{self.provider.server_port}/v1",
            "model": "guidance-alias-fixture", "api_key": "", "daily_limit": 20,
            "parameters": {"temperature": 0, "max_tokens": 800},
        }, other_project)
        self.assertEqual(status, 200)
        changed = copy.deepcopy(raw)
        for section in ("summary", "handoff"):
            changed[section]["evidence"][0]["source_chapter_id"] = other_chapter
        changed["candidates"][0].update({
            "source_chapter_id": other_chapter, "known_by": [person["id"]],
        })
        second = self.complete(changed, other_project, other_chapter)
        self.assertNotEqual(second["candidates"][0]["id"], first["candidates"][0]["id"])
        status, response = self.confirm(second, project=other_project)
        self.assertEqual(status, 200, response)
        other_candidate = response["analysis"]["candidates"][0]
        database, calls = self.database_hash(), len(Provider.requests)
        cross_status, rejected = self.confirm(first, project=other_project)
        self.observe("other_book_control", own_confirm_http_status=status,
                     foreign_confirm_http_status=cross_status,
                     foreign_confirm_error=rejected.get("error"),
                     original_book_canonical_unchanged=self.canonical() == before,
                     foreign_artifact_ids_disjoint=not (
                         set(artifact_ids(accepted).values()) & set(artifact_ids(other_candidate).values())),
                     foreign_confirm_database_unchanged=self.database_hash() == database,
                     original_manuscript_and_revisions_unchanged=self.manuscript() == self.original_manuscript,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertIn(cross_status, (400, 404))
        self.assertEqual(self.database_hash(), database)
        self.assertEqual(self.canonical(), before)
        self.assertFalse(set(artifact_ids(accepted).values()) &
                         set(artifact_ids(other_candidate).values()))
        self.assertEqual(self.manuscript(), self.original_manuscript)
        self.assertEqual(len(Provider.requests), calls)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def freeze_sources(phase, expected):
    capture = os.environ.get("MOYU_ANALYSIS_CANDIDATE_REUSE_CAPTURE")
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
    output = os.environ.get("MOYU_ANALYSIS_CANDIDATE_REUSE_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; choose a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    before_capture = freeze_sources("before", before)
    Provider.total_calls = 0
    AnalysisCandidateReuseHTTP.observations = []
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisCandidateReuseHTTP))
    after = fingerprints()
    after_capture = freeze_sources("after", after)
    report = safe_report({
        "passed": result.wasSuccessful() and before == after,
        "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(AnalysisCandidateReuseHTTP.observations),
        "synthetic_only": True, "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "frozen_source_before": before_capture, "frozen_source_after": after_capture,
        "source_unchanged": before == after,
        "scope": "temporary synthetic SQLite; actual saved AI jobs and reports; real loopback HTTP; free local provider",
        "baseline_failure": "unchanged cross-report candidates currently return HTTP 409 UNIQUE candidate_memory.id; existing rollback prevents duplicate or partial data; not HTTP 500",
        "claim_limit": "canonical candidate reuse and conflict integrity only; no real manuscript, credential, literary-quality or first-tier claim",
    })
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
