#!/usr/bin/env python3
"""Schema-valid legacy evidence stays readable without losing real dependencies."""
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

from guidance_alias_http_fixtures import GuidanceAliasHTTP, Provider
from guidance_scope_change_http_fixtures import EvidenceResult
from material_dependency_http_fixtures import MaterialDependencyHTTP, CANARY
import server


PROJECT = "default-project"
OTHER_CANARY = "OMITTED_OTHER_MEMORY_EVIDENCE_CANARY"
LEGACY_NOTE = "legacy free-text evidence note"
SOURCES = (
    "moyu_materials.py", "server.py", "moyu_analysis.py", "moyu_context.py",
    "moyu_workspace.py", "moyu_records.py", "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py",
    "tests/material_dependency_http_fixtures.py",
    "tests/material_evidence_compat_http_fixtures.py",
    "docs/schema-v1.1-migration.sql",
)


class MaterialEvidenceCompatibilityHTTP(unittest.TestCase):
    observations = []
    queue = GuidanceAliasHTTP.queue
    process = GuidanceAliasHTTP.process
    preview = GuidanceAliasHTTP.preview
    body = GuidanceAliasHTTP.body
    state = MaterialDependencyHTTP.state
    change = MaterialDependencyHTTP.change
    prior = MaterialDependencyHTTP.prior
    memory = MaterialDependencyHTTP.memory
    frozen = MaterialDependencyHTTP.frozen
    assert_dispatch_blocked = MaterialDependencyHTTP.assert_dispatch_blocked

    def setUp(self):
        self.exchanges = []
        GuidanceAliasHTTP.setUp(self)
        status, chapter = self.call("POST", "/api/chapters", {
            "title": "证据兼容当前章", "body": "林岚等在北窗，指尖抵住铜扣。", "pov": "林岚",
        })
        self.assertEqual(status, 201, chapter)
        self.chapter_id = chapter["id"]
        self.request["chapter_id"] = self.chapter_id
        self.request["retrieval_profile"] = {
            "chapter_top_k": 0, "recent_window": 0, "memory_top_k": 10, "record_top_k": 10,
        }

    def tearDown(self):
        capture = os.environ.get("MOYU_MATERIAL_EVIDENCE_COMPAT_CAPTURE")
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

    def legacy_candidate(self, identifier, entries):
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,"
                "object_json,epistemic_mode,state,confidence,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (identifier, PROJECT, "main", self.person["id"], "legacy_note", "{}",
                 "inference", "candidate", 0.5, server.json_text(entries)),
            )
            saved = conn.execute(
                "SELECT state,json_valid(evidence_json) valid FROM assertion WHERE id=?", (identifier,),
            ).fetchone()
        self.assertEqual(saved["state"], "candidate")
        self.assertEqual(saved["valid"], 1)

    def set_assertion_state(self, identifier, state):
        with server.connect() as conn:
            conn.execute("UPDATE assertion SET state=? WHERE id=? AND project_id=? AND branch_id=?",
                         (state, identifier, PROJECT, "main"))

    def assertion_snapshot(self, identifier):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT id,state,evidence_json,reviewed_by,reviewed_at,"
                "json_valid(evidence_json) valid,json_type(evidence_json) evidence_type "
                "FROM assertion WHERE id=? AND project_id=? AND branch_id=?",
                (identifier, PROJECT, "main"),
            ).fetchone()
        self.assertIsNotNone(row)
        value = dict(row)
        value["evidence"] = json.loads(value.pop("evidence_json"))
        return value

    def canon_mixed_memory(self):
        memory = self.memory()
        identifier = memory["assertion_id"]
        previous = self.assertion_snapshot(identifier)
        self.assertEqual(previous["state"], "canon")
        self.assertTrue(previous["reviewed_by"])
        self.assertTrue(previous["reviewed_at"])
        linked = [item for item in previous["evidence"]
                  if isinstance(item, dict) and item.get("candidate_memory_id") == memory["id"]]
        self.assertEqual(len(linked), 1)
        self.assertTrue(linked[0].get("source_span_id"))
        entries = [
            LEGACY_NOTE, 8, None, True, ["nested legacy note"],
            server.json_text({"candidate_memory_id": "legacy_string_is_not_a_reference"}),
            *previous["evidence"],
        ]
        with server.connect() as conn:
            triggers = [row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name IN ('check_assertion_canon_insert','check_assertion_canon_update') "
                "ORDER BY name",
            )]
            self.assertEqual(triggers, ["check_assertion_canon_insert", "check_assertion_canon_update"])
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            conn.execute("UPDATE assertion SET evidence_json=? WHERE id=? AND project_id=? AND branch_id=?",
                         (server.json_text(entries), identifier, PROJECT, "main"))
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
        saved = self.assertion_snapshot(identifier)
        self.assertEqual(saved["state"], "canon")
        self.assertEqual(saved["valid"], 1)
        self.assertEqual(saved["evidence_type"], "array")
        self.assertEqual(saved["evidence"], entries)
        self.assertEqual(saved["reviewed_by"], previous["reviewed_by"])
        self.assertEqual(saved["reviewed_at"], previous["reviewed_at"])
        self.observe("schema_valid_mixed_canon", assertion_id=identifier,
                     evidence_entries=len(entries), canon_guards_present=triggers,
                     reviewed_identity_and_time_preserved=True, foreign_keys_valid=True)
        return memory, saved

    def test_unrelated_legacy_string_evidence_keeps_history_and_dispatch_usable(self):
        self.memory()
        job = self.queue()
        context, _ = self.frozen(job)
        self.legacy_candidate("legacy_unrelated_string", [LEGACY_NOTE])
        self.assertEqual(self.call("GET", "/api/state")[0], 200)
        status, history = self.call("GET", "/api/ai/jobs/" + job["id"])
        self.observe("legacy_string_history", history_http_status=status, history_error=history.get("error"),
                     free_provider_calls=len(Provider.requests))
        self.assertEqual(status, 200, history)
        self.assertFalse(history.get("materials_read_only"), history)
        self.assertIn(CANARY, self.preview()["user"])
        completed = self.process(job)
        current_context, _ = self.frozen(job)
        self.observe("legacy_string_dispatch", dispatch_status=completed["status"],
                     frozen_context_unchanged=current_context == context,
                     free_provider_calls=len(Provider.requests))
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(current_context, context)
        self.assertEqual(len(Provider.requests), 1)
        self.assertNotIn(LEGACY_NOTE, json.dumps(Provider.requests, ensure_ascii=False))

    def test_mixed_evidence_preserves_the_actual_memory_dependency(self):
        memory = self.memory()
        identifier = "legacy_mixed_real_memory"
        self.legacy_candidate(identifier, [
            LEGACY_NOTE, 8, None, True, ["nested legacy note"],
            {"candidate_memory_id": memory["id"]},
        ])
        self.assertEqual(self.call("GET", "/api/state")[0], 200)
        status, preview = self.call("POST", "/api/ai/prompt-preview", self.request)
        self.observe("mixed_evidence_capture", preview_http_status=status,
                     preview_error=preview.get("error"), free_provider_calls=len(Provider.requests))
        self.assertEqual(status, 200, preview)
        self.assertIn(CANARY, preview["user"])
        job = self.queue()
        self.set_assertion_state(identifier, "retracted")
        self.assert_dispatch_blocked(job, "mixed_evidence_actual_memory_state")

    def test_mixed_evidence_for_omitted_other_memory_does_not_expire_this_task(self):
        consumed = self.memory()
        other = self.memory()
        self.change("/api/memory/" + other["id"], note=OTHER_CANARY)
        self.request["material_controls"] = {"memory": {other["id"]: {"omit": True}}}
        identifier = "legacy_mixed_other_memory"
        self.legacy_candidate(identifier, [LEGACY_NOTE, {"candidate_memory_id": other["id"]}, 12])
        status, preview = self.call("POST", "/api/ai/prompt-preview", self.request)
        self.observe("other_memory_capture", preview_http_status=status, preview_error=preview.get("error"))
        self.assertEqual(status, 200, preview)
        self.assertIn(CANARY, preview["user"])
        self.assertNotIn(OTHER_CANARY, preview["user"])
        job = self.queue()
        context, _ = self.frozen(job)
        consumed_ids = [item["id"] for item in context["confirmed_memory"]]
        self.assertIn(consumed["id"], consumed_ids)
        self.assertNotIn(other["id"], consumed_ids)
        self.set_assertion_state(identifier, "retracted")
        self.change("/api/memory/" + other["id"], note=OTHER_CANARY + "_CHANGED")
        completed = self.process(job)
        sent = json.dumps(Provider.requests, ensure_ascii=False)
        self.observe("other_memory_control", dispatch_status=completed["status"],
                     free_provider_calls=len(Provider.requests), other_memory_absent=OTHER_CANARY not in sent)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertIn(CANARY, sent)
        self.assertNotIn(OTHER_CANARY, sent)

    def test_real_linked_assertion_state_change_still_blocks_old_dispatch(self):
        memory = self.memory()
        job = self.queue()
        with server.connect() as conn:
            linked = conn.execute(
                "SELECT a.id FROM assertion a JOIN json_each(a.evidence_json) e "
                "WHERE a.project_id=? AND a.branch_id=? AND a.state='canon' "
                "AND CASE WHEN e.type='object' "
                "THEN json_extract(e.value,'$.candidate_memory_id') END=?",
                (PROJECT, "main", memory["id"]),
            ).fetchone()
        self.assertIsNotNone(linked)
        self.set_assertion_state(linked["id"], "retracted")
        self.assert_dispatch_blocked(job, "real_linked_assertion_state")

    def test_canon_mixed_evidence_keeps_story_map_preview_and_history_usable(self):
        memory, assertion = self.canon_mixed_memory()
        status, story = self.call("GET", "/api/story-map")
        self.observe("mixed_canon_story_map", http_status=status, error=story.get("error"))
        self.assertEqual(status, 200, story)
        preview = self.preview()
        self.assertIn(CANARY, preview["user"])
        self.assertNotIn(LEGACY_NOTE, preview["user"])
        job = self.queue()
        context, _ = self.frozen(job)
        self.assertIn(memory["id"], [item["id"] for item in context["confirmed_memory"]])
        status, history = self.call("GET", "/api/ai/jobs/" + job["id"])
        self.observe("mixed_canon_history", http_status=status,
                     history_read_only=history.get("materials_read_only"),
                     free_provider_calls=len(Provider.requests))
        self.assertEqual(status, 200, history)
        self.assertFalse(history.get("materials_read_only"), history)
        self.assertEqual(self.assertion_snapshot(assertion["id"]), assertion)
        self.assertEqual(len(Provider.requests), 0)

    def test_canon_mixed_evidence_keeps_memory_details_and_lists_readable(self):
        memory, assertion = self.canon_mixed_memory()
        routes = (
            "/api/memory/" + memory["id"],
            "/api/memory/" + memory["id"] + "/evidence",
            "/api/assertions?memory_id=" + memory["id"],
            "/api/assertions/" + assertion["id"],
            "/api/memory",
        )
        responses = {}
        for route in routes:
            with self.subTest(route=route):
                status, response = self.call("GET", route)
                self.observe("mixed_canon_read", route=route, http_status=status,
                             error=response.get("error"))
                self.assertEqual(status, 200, response)
                responses[route] = response
        self.assertEqual(len(responses), len(routes))
        detail = responses[routes[0]]
        self.assertEqual(detail["memory"]["id"], memory["id"])
        self.assertEqual(detail["memory"]["assertion_id"], assertion["id"])
        self.assertEqual(detail["memory"]["status_raw"], "accepted")
        self.assertFalse(detail["memory"]["stale"])
        for route in routes[:3]:
            self.assertIn(assertion["id"], [item["id"] for item in responses[route]["assertions"]])
        assertion_detail = responses[routes[3]]
        self.assertEqual(assertion_detail["state"], "canon")
        self.assertEqual(assertion_detail["reviewed_by"], assertion["reviewed_by"])
        self.assertEqual(assertion_detail["reviewed_at"], assertion["reviewed_at"])
        self.assertIn(memory["id"], [item["id"] for item in responses[routes[4]]["memory"]])
        for route in (routes[0], routes[1], routes[3]):
            evidence = responses[route]["evidence"]
            linked = [item for item in evidence if item.get("candidate_memory_id") == memory["id"]]
            self.assertEqual(len(linked), 1)
            self.assertEqual(linked[0]["candidate_memory"]["id"], memory["id"])
            self.assertEqual(linked[0]["source_span"]["quote"], memory["source_quote"])
            self.assertTrue(linked[0]["source_span"]["current"])
            self.assertFalse(linked[0]["stale"])
            notes = [item for item in evidence if item.get("value") == LEGACY_NOTE]
            self.assertEqual(len(notes), 1)
            self.assertIn("unsupported_evidence", notes[0]["stale_reasons"])
        self.assertEqual(self.assertion_snapshot(assertion["id"]), assertion)
        self.assertEqual(len(Provider.requests), 0)

    def test_canon_mixed_evidence_withdrawal_retracts_fact_and_blocks_old_dispatch(self):
        memory, assertion = self.canon_mixed_memory()
        job = self.queue()
        status, changed = self.call("PATCH", "/api/memory/" + memory["id"], {
            "status": "rejected", "reason": "合成作者撤回，验证历史证据兼容。",
        })
        self.observe("mixed_canon_withdrawal", http_status=status, error=changed.get("error"),
                     memory_status=changed.get("status_raw"))
        self.assertEqual(status, 200, changed)
        self.assertEqual(changed["status_raw"], "rejected")
        after = self.assertion_snapshot(assertion["id"])
        self.assertEqual(after["state"], "retracted")
        self.assertEqual(after["evidence"], assertion["evidence"])
        self.assertNotIn(CANARY, self.preview()["user"])
        self.assert_dispatch_blocked(job, "mixed_canon_withdrawn_dependency")

    def test_canon_mixed_evidence_removed_quote_invalidates_fact_and_old_dispatch(self):
        memory, assertion = self.canon_mixed_memory()
        job = self.queue()
        status, chapter = self.call("PATCH", "/api/chapters/" + memory["source_chapter_id"], {
            "body": "北窗空着，石阶没有来客，也没有铜扣的交接。",
        })
        self.observe("mixed_canon_source_removed", http_status=status, error=chapter.get("error"))
        self.assertEqual(status, 200, chapter)
        self.assertNotIn(memory["source_quote"], chapter["body"])
        status, detail = self.call("GET", "/api/memory/" + memory["id"])
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["memory"]["status_raw"], "stale")
        self.assertTrue(detail["memory"]["stale"])
        self.assertEqual(detail["memory"]["source_span_status"], "stale")
        after = self.assertion_snapshot(assertion["id"])
        self.assertEqual(after["state"], "candidate")
        self.assertEqual(after["evidence"], assertion["evidence"])
        self.assertNotIn(CANARY, self.preview()["user"])
        self.assert_dispatch_blocked(job, "mixed_canon_removed_source_dependency")

    def test_canon_mixed_evidence_unique_quote_reanchors_and_preserves_notes(self):
        memory, assertion = self.canon_mixed_memory()
        old_link = next(item for item in assertion["evidence"]
                        if isinstance(item, dict) and item.get("candidate_memory_id") == memory["id"])
        job = self.queue()
        prefix = "晨光压过窗棂。\n"
        status, chapter = self.call("PATCH", "/api/chapters/" + memory["source_chapter_id"], {
            "body": prefix + memory["source_quote"] + "\n雨停了。",
        })
        self.observe("mixed_canon_reanchor", http_status=status, error=chapter.get("error"))
        self.assertEqual(status, 200, chapter)
        status, detail = self.call("GET", "/api/memory/" + memory["id"])
        self.assertEqual(status, 200, detail)
        current = detail["memory"]
        self.assertEqual(current["status_raw"], "accepted")
        self.assertEqual(current["source_span_status"], "valid")
        self.assertFalse(current["stale"])
        self.assertEqual(current["source_start"], len(prefix))
        self.assertEqual(current["source_end"], len(prefix) + len(memory["source_quote"]))
        self.assertNotEqual(current["source_span_id"], old_link["source_span_id"])
        after = self.assertion_snapshot(assertion["id"])
        expected = copy.deepcopy(assertion["evidence"])
        for item in expected:
            if isinstance(item, dict) and item.get("source_span_id") == old_link["source_span_id"]:
                item["source_span_id"] = current["source_span_id"]
        self.assertEqual(after["state"], "canon")
        self.assertEqual(after["evidence"], expected)
        self.assertEqual(after["reviewed_by"], assertion["reviewed_by"])
        self.assertEqual(after["reviewed_at"], assertion["reviewed_at"])
        with server.connect() as conn:
            previous_status = conn.execute("SELECT status FROM source_span WHERE id=?",
                                           (old_link["source_span_id"],)).fetchone()[0]
        self.assertEqual(previous_status, "stale")
        self.assert_dispatch_blocked(job, "mixed_canon_reanchored_source_dependency")
        self.assertIn(CANARY, self.preview()["user"])
        fresh = self.queue()
        context, _ = self.frozen(fresh)
        consumed = next(item for item in context["confirmed_memory"] if item["id"] == memory["id"])
        self.assertEqual(consumed["source_span_id"], current["source_span_id"])
        completed = self.process(fresh)
        sent = json.dumps(Provider.requests, ensure_ascii=False)
        self.observe("mixed_canon_fresh_reanchored_dispatch", dispatch_status=completed["status"],
                     old_span_status=previous_status, legacy_entries_preserved=after["evidence"] == expected,
                     free_provider_calls=len(Provider.requests))
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertIn(CANARY, sent)
        self.assertNotIn(LEGACY_NOTE, sent)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def freeze_sources(phase, expected):
    capture = os.environ.get("MOYU_MATERIAL_EVIDENCE_COMPAT_CAPTURE")
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
    output = os.environ.get("MOYU_MATERIAL_EVIDENCE_COMPAT_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    before_capture = freeze_sources("before", before)
    Provider.total_calls = 0
    MaterialEvidenceCompatibilityHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MaterialEvidenceCompatibilityHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    after_capture = freeze_sources("after", after)
    report = {
        "passed": result.wasSuccessful(), "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(MaterialEvidenceCompatibilityHTTP.observations),
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "frozen_source_before": before_capture, "frozen_source_after": after_capture,
        "source_unchanged": before == after,
        "scope": "schema-valid synthetic candidate and reviewed canon legacy evidence; temporary SQLite; real loopback HTTP; free local provider",
        "claim_limit": "material evidence compatibility and dependency integrity only; no real manuscript, credential, or literary-quality claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
