#!/usr/bin/env python3
"""Read legacy evidence safely without rewriting accepted history."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chapter_deletion_dependency_http_fixtures as seed  # noqa: E402


server = seed.server
SOURCES = (
    "backend/server.py", "backend/moyu_context.py", "backend/moyu_materials.py", "backend/moyu_backups.py",
    "backend/moyu_portable.py", "backend/moyu_records.py", "backend/moyu_workspace.py",
    "backend/moyu_agent_chat.py", "backend/moyu_agent_settings.py",
    "tests/chapter_deletion_dependency_http_fixtures.py",
    "tests/legacy_deleted_dependency_http_fixtures.py",
)
EXPECTED_TABLES = {
    "actor", "ai_run", "app_agent_chat_action", "app_agent_chat_session",
    "app_agent_chat_turn", "app_agent_checkpoint", "app_agent_connection",
    "app_agent_session", "app_agent_write", "app_ai_chunk", "app_ai_job",
    "app_ai_run_decision", "app_assistant_request", "app_backup_artifact",
    "app_blueprint", "app_candidate_application", "app_candidate_revision",
    "app_chapter_analysis", "app_chapter_commit", "app_chapter_meta",
    "app_chapter_structure", "app_deleted_node", "app_entity_meta",
    "app_entity_scope", "app_foreshadow_link", "app_manuscript_import",
    "app_model_settings", "app_planning_application", "app_planning_attempt",
    "app_planning_event", "app_planning_workflow", "app_plot_item",
    # 提示词工坊（2026-10-23）新增的内置/自定义模板表，属于当前 schema。
    "app_prompt_template",
    "app_project_meta", "app_quota_reservation", "app_reference_document",
    "app_reference_section", "app_search_document", "app_search_fts",
    "app_search_fts_config", "app_search_fts_content", "app_search_fts_data",
    "app_search_fts_docsize", "app_search_fts_idx", "app_search_index_state",
    "app_state_version", "app_story_relation", "app_story_review",
    "app_timeline_event", "app_workspace", "assertion", "audit_event",
    "branch", "candidate_memory", "character_state", "deletion_event",
    "document_node", "document_revision", "entity", "event_log", "export_job",
    "external_source", "index_checkpoint", "merge_conflict", "migration_ledger",
    "plot_thread", "project", "prov_activity", "prov_agent", "prov_edge",
    "prov_entity", "run_manifest", "source_span",
}
REPORT = {
    "observations": [], "http": [], "attempted_provider_calls": 0,
    "attempted_worker_calls": 0, "initialization_integrity": [],
    "restore_integrity": [], "scope_restore_integrity": [],
}


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in SOURCES}


def database_snapshot():
    path = server.DB_PATH.resolve()
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {
            "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "schema": [tuple(row) for row in conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "ORDER BY type,name,tbl_name")],
            "tables": {name: [tuple(row) for row in conn.execute(
                f'SELECT * FROM "{name}"')] for name in names},
        }
    finally:
        conn.rollback()
        conn.close()


def database_digest(value):
    def blob(item):
        if isinstance(item, bytes):
            return {"$blob": base64.b64encode(item).decode("ascii")}
        raise TypeError("Unsupported synthetic snapshot value")
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True,
                     separators=(",", ":"), default=blob, allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise AssertionError("Synthetic loopback requests must not redirect")


class LegacyDeletedDependenciesHTTP(unittest.TestCase):
    def setUp(self):
        self.fixture = seed.ChapterDeletionDependenciesHTTP(
            "test_delete_api_withdraws_facts_and_keeps_other_material")
        self.fixture.setUp()
        self.fixture._testMethodName = self._testMethodName
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), RefuseRedirect())

    def tearDown(self):
        provider = self.fixture.provider.call_count
        worker = self.fixture.worker.call_count
        try:
            self.fixture.tearDown()
        finally:
            REPORT["attempted_provider_calls"] += provider
            REPORT["attempted_worker_calls"] += worker

    def call(self, method, path, payload=None, *, project=seed.PROJECT,
             branch=seed.BRANCH, binary=False):
        request = urllib.request.Request(
            self.fixture.base + path, method=method,
            data=server.json_text(payload).encode("utf-8")
            if payload is not None else None,
            headers={"Content-Type": "application/json",
                     "X-Moyu-Project": project, "X-Moyu-Branch": branch})
        try:
            with self.opener.open(request, timeout=10) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        result = raw if binary and status == 200 else json.loads(raw)
        REPORT["http"].append({
            "test": self._testMethodName, "method": method, "path": path,
            "status": status,
            "error": result.get("error") if isinstance(result, dict) else None,
        })
        return status, result

    def set_alias_conflict(self):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM candidate_memory "
                "WHERE id='memory-delete-review'").fetchone()
            payload = json.loads(row["payload_json"])
            payload["name"] = "historical-author-label"
            conn.execute(
                "UPDATE candidate_memory SET payload_json=?,confidence=0.75 "
                "WHERE id='memory-delete-review'", (server.json_text(payload),))
            # Isolate conflict cards from the separate linked-state path.
            conn.execute("DELETE FROM character_state "
                         "WHERE id='character-delete-review'")

    def append_assertion_evidence(self, *entries):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT evidence_json FROM assertion WHERE id=?",
                (self.fixture.assertion_id,)).fetchone()
            evidence = json.loads(row["evidence_json"])
            conn.execute("UPDATE assertion SET evidence_json=? WHERE id=?",
                         (server.json_text(evidence + list(entries)),
                          self.fixture.assertion_id))

    def source_span_id(self, memory_id="memory-delete-review"):
        with server.connect() as conn:
            return conn.execute(
                "SELECT source_span_id FROM candidate_memory WHERE id=?",
                (memory_id,)).fetchone()[0]

    def legacy_delete(self):
        with server.connect() as conn:
            head = conn.execute(
                "SELECT head_revision FROM branch WHERE id=?",
                (seed.BRANCH,)).fetchone()[0]
            conn.execute(
                "INSERT INTO deletion_event(id,project_id,branch_id,aggregate_id,"
                "revision_id,reason,scope,created_at,actor_id) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (server.uid("del_"), seed.PROJECT, seed.BRANCH,
                 self.fixture.chapter_id, self.fixture.source_revision[0],
                 "synthetic legacy deletion", "document", server.now(), seed.ACTOR))
            conn.execute(
                "INSERT INTO app_deleted_node(chapter_id,deleted_at,reason) "
                "VALUES(?,?,?)",
                (self.fixture.chapter_id, server.now(), "synthetic legacy deletion"))
            server.append_event(
                conn, self.fixture.chapter_id, "chapter.deleted",
                {"reason": "synthetic legacy deletion"}, head)
        before = database_snapshot()
        server.init_db()
        after = database_snapshot()
        REPORT["initialization_integrity"].append({
            "test": self._testMethodName,
            "compared_tables": len(before["tables"]),
            "all_tables_and_schema_unchanged": before == after,
            "database_sha256_before": database_digest(before),
            "database_sha256_after": database_digest(after),
        })
        with self.subTest(check="legacy_initialization_preserves_history"):
            self.assertEqual(set(before["tables"]), EXPECTED_TABLES)
            self.assertEqual(before, after)

    def restore_zip(self):
        physical_before = self.physical_evidence(seed.PROJECT, seed.BRANCH)
        status, result = self.call("POST", "/api/backups", {
            "name": "synthetic legacy evidence"})
        self.assertEqual(status, 201, result)
        status, archive = self.call(
            "GET", "/api/backups/" + result["id"] + "/download", binary=True)
        self.assertEqual(status, 200)
        status, preview = self.call("POST", "/api/backups/import/preview", {
            "filename": "synthetic-legacy-evidence.zip",
            "data_base64": base64.b64encode(archive).decode("ascii"),
        })
        self.assertEqual(status, 200, preview)
        status, result = self.call("POST", "/api/backups/import/apply", {
            "preview_id": preview["preview_id"],
            "archive_sha256": preview["archive_sha256"],
            "title": "Synthetic legacy evidence restored", "confirm": True,
        })
        self.assertEqual(status, 201, result)
        physical_after = self.physical_evidence(
            result["project_id"], result["branch_id"])
        invariant_fields = (
            "memory_status", "confidence", "reviewed_at", "span_status", "deleted",
            "span_quote", "span_quote_hash", "assertion_state",
            "assertion_confidence", "assertion_reviewed_at",
            "memory_actor_kind", "memory_actor_name",
            "assertion_actor_kind", "assertion_actor_name",
        )
        before_values = {key: physical_before[key] for key in invariant_fields}
        after_values = {key: physical_after[key] for key in invariant_fields}
        before_payload = json.loads(physical_before["payload_json"])
        after_payload = json.loads(physical_after["payload_json"])
        assertion_object_preserved = (
            json.loads(physical_before["assertion_object"]) ==
            json.loads(physical_after["assertion_object"]))
        payload_fields = ("name", "key", "type", "note", "source_quote",
                          "source_start", "source_end", "source_hash")
        payload_preserved = all(before_payload.get(key) == after_payload.get(key)
                                for key in payload_fields)
        actors_remapped = all(
            physical_after[key] == result["project_id"] for key in
            ("memory_actor_project", "assertion_actor_project"))
        actors_present = all(physical_after[key] for key in
                             ("reviewed_by", "assertion_reviewed_by"))
        REPORT["restore_integrity"].append({
            "test": self._testMethodName,
            "historical_status_confidence_review_and_actor_identity_preserved":
                before_values == after_values and assertion_object_preserved,
            "historical_author_payload_preserved": payload_preserved,
            "review_authors_exist_in_restored_project":
                actors_remapped and actors_present,
            "chapter_reference_remapped":
                after_payload.get("source_chapter_id") == physical_after["chapter_id"],
            "source_and_restored_actor_ids_are_distinct":
                physical_before["reviewed_by"] != physical_after["reviewed_by"],
        })
        with self.subTest(check="zip_restore_preserves_review_history"):
            self.assertEqual(before_values, after_values)
            self.assertTrue(assertion_object_preserved)
            self.assertTrue(payload_preserved)
            self.assertTrue(actors_present)
            self.assertTrue(actors_remapped)
            self.assertNotEqual(
                physical_before["reviewed_by"], physical_after["reviewed_by"])
            self.assertEqual(
                after_payload["source_chapter_id"], physical_after["chapter_id"])
        return result["project_id"], result["branch_id"]

    def physical_evidence(self, project, branch):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT cm.id memory_id,cm.status memory_status,"
                "cm.payload_json,cm.confidence,cm.reviewed_at,cm.reviewed_by,"
                "ss.id span_id,ss.status span_status,ss.revision_id,"
                "ss.quote span_quote,ss.quote_hash span_quote_hash,"
                "ss.document_node_id chapter_id,a.id assertion_id,a.state assertion_state,"
                "a.evidence_json assertion_evidence,a.object_json assertion_object,"
                "a.confidence assertion_confidence,a.reviewed_at assertion_reviewed_at,"
                "a.reviewed_by assertion_reviewed_by,"
                "ma.kind memory_actor_kind,ma.name memory_actor_name,"
                "ma.project_id memory_actor_project,"
                "aa.kind assertion_actor_kind,aa.name assertion_actor_name,"
                "aa.project_id assertion_actor_project,"
                "EXISTS(SELECT 1 FROM app_deleted_node d "
                "WHERE d.chapter_id=ss.document_node_id) deleted "
                "FROM candidate_memory cm JOIN source_span ss ON ss.id=cm.source_span_id "
                "JOIN assertion a ON a.project_id=cm.project_id AND a.branch_id=cm.branch_id "
                "JOIN json_each(a.evidence_json) e "
                "ON CASE WHEN e.type='object' THEN "
                "json_extract(e.value,'$.candidate_memory_id') END=cm.id "
                "LEFT JOIN actor ma ON ma.id=cm.reviewed_by "
                "LEFT JOIN actor aa ON aa.id=a.reviewed_by "
                "WHERE cm.project_id=? AND cm.branch_id=? "
                "AND json_extract(cm.payload_json,'$.key')='memory-delete-review'",
                (project, branch)).fetchone()
            self.assertIsNotNone(row)
            return dict(row)

    def observe(self, scenario, *, project=seed.PROJECT, branch=seed.BRANCH,
                stale, conflict=False, deleted=False,
                memory_status="accepted", span_status="valid",
                assertion_stale=None, memory_stale=None, memory_consumed=None):
        assertion_stale = stale if assertion_stale is None else assertion_stale
        memory_stale = stale if memory_stale is None else memory_stale
        memory_consumed = not stale if memory_consumed is None else memory_consumed
        before = database_snapshot()
        physical = self.physical_evidence(project, branch)
        state_status, state = self.call("GET", "/api/state", project=project, branch=branch)
        chars_status, chars = self.call(
            "GET", "/api/character-states", project=project, branch=branch)
        story_status, story = self.call(
            "GET", "/api/story-map", project=project, branch=branch)
        assertions_status, assertions = self.call(
            "GET", "/api/assertions?state=canon", project=project, branch=branch)
        for status, result in (
                (state_status, state), (chars_status, chars),
                (story_status, story), (assertions_status, assertions)):
            self.assertEqual(status, 200, result)
        chapter = next(item for item in state["chapters"]
                       if seed.KEEP_CANARY in item["body"])
        preview_status, preview = self.call("POST", "/api/ai/prompt-preview", {
            "task": "continue", "chapter_id": chapter["id"],
            "instruction": "Continue the synthetic evidence control",
            "retrieval_profile": {"chapter_top_k": 10, "recent_window": 0,
                                  "memory_top_k": 10, "record_top_k": 0},
        }, project=project, branch=branch)
        after = database_snapshot()
        character = next((item for item in chars["character_states"]
                          if item["attribute"] == "character-delete-review"), None)
        conflict_card = next((item for item in story["conflicts"]
                              if item["id"] == physical["assertion_id"]), None)
        assertion = next(item for item in assertions["assertions"]
                         if item["id"] == physical["assertion_id"])
        public_memory = next(item for item in state["memory"]
                             if item["id"] == physical["memory_id"])
        observation = {
            "test": self._testMethodName, "scenario": scenario,
            "public_state_status": state_status, "character_states_status": chars_status,
            "story_map_status": story_status, "assertions_status": assertions_status,
            "physical": {key: physical[key] for key in (
                "memory_status", "span_status", "assertion_state", "deleted",
                "confidence", "reviewed_at", "reviewed_by")},
            "memory_name": json.loads(physical["payload_json"])["name"],
            "public_memory_stale": public_memory["stale"],
            "assertion_stale": assertion["stale"],
            "expected_assertion_stale": assertion_stale,
            "expected_material_stale": stale,
            "expected_memory_stale": memory_stale,
            "expected_memory_consumed": memory_consumed,
            "character_stale": character.get("stale") if character else None,
            "conflict_present": conflict_card is not None,
            "conflict_stale": conflict_card.get("stale") if conflict_card else None,
            "preview_status": preview_status, "preview_error": preview.get("error"),
            "canary_in_prompt": seed.CANARY in preview.get("user", ""),
            "compared_tables": len(before["tables"]),
            "all_tables_and_schema_unchanged": before == after,
            "database_sha256_before": database_digest(before),
            "database_sha256_after": database_digest(after),
        }
        REPORT["observations"].append(observation)
        with self.subTest(check="all_expected_tables_and_schema_are_read_only"):
            self.assertEqual(set(before["tables"]), EXPECTED_TABLES)
            self.assertEqual(before, after)
            self.assertIn("app_search_fts_data", before["tables"])
            self.assertIn("app_search_fts_idx", before["tables"])
        with self.subTest(check="physical_history_is_preserved"):
            self.assertEqual(physical["memory_status"], memory_status)
            self.assertEqual(physical["span_status"], span_status)
            self.assertEqual(physical["assertion_state"], "canon")
            self.assertEqual(bool(physical["deleted"]), deleted)
            self.assertEqual(self.physical_evidence(project, branch), physical)
            self.assertEqual(
                physical["chapter_id"] in {item["id"] for item in state["chapters"]},
                not deleted)
        with self.subTest(check="unrelated_character_states_remain_usable"):
            author = next(item for item in chars["character_states"]
                          if item["attribute"] == "character-author-review")
            retained = next(item for item in chars["character_states"]
                            if item["attribute"] == "character-retained-review")
            self.assertIsNone(author["source_assertion_id"])
            self.assertFalse(author["stale"])
            self.assertFalse(retained["stale"])
        with self.subTest(check="derived_evidence_state"):
            self.assertEqual(assertion["stale"], assertion_stale)
            self.assertEqual(public_memory["stale"], memory_stale)
            if conflict:
                self.assertIsNone(character)
                self.assertIsNotNone(conflict_card)
                self.assertEqual(bool(conflict_card.get("stale")), stale)
            else:
                self.assertIsNotNone(character)
                self.assertEqual(character["stale"], stale)
        with self.subTest(check="new_prompt_is_available"):
            self.assertEqual(preview_status, 200, preview)
        if preview_status == 200:
            material = json.loads(preview["user"].split("<manuscript>\n", 1)[1]
                                  .split("\n</manuscript>", 1)[0])
            with self.subTest(check="only_current_evidence_is_consumed"):
                if deleted or not stale:
                    self.assertEqual(seed.CANARY in preview["user"], not stale)
                self.assertIn(seed.KEEP_CANARY, preview["user"])
                self.assertIn(seed.AUTHOR_CANARY, preview["user"])
                selected = material["story_structure"]
                self.assertEqual(
                    physical["assertion_id"] in {
                        item["id"] for item in selected["conflicts"]},
                    conflict and not stale)
                self.assertEqual(
                    any(item["attribute"] == "character-delete-review"
                        for item in selected["character_states"]),
                    not conflict and not stale)
                self.assertEqual(
                    physical["memory_id"] in {
                        item["id"] for item in material["confirmed_memory"]},
                    memory_consumed)
            observation["memory_consumed"] = physical["memory_id"] in {
                item["id"] for item in material["confirmed_memory"]}
            observation["character_consumed"] = any(
                item["attribute"] == "character-delete-review"
                for item in material["story_structure"]["character_states"])
            observation["conflict_consumed"] = physical["assertion_id"] in {
                item["id"] for item in material["story_structure"]["conflicts"]}
        return observation

    def create_span_story(self, collection, *, memory_id="memory-delete-review",
                          declared_chapter=None, author_plan=False):
        span_id = self.source_span_id(memory_id)
        evidence = {} if author_plan else {"source_span_id": span_id}
        if declared_chapter is not None:
            evidence["source_chapter_id"] = declared_chapter
        identifier = "legacy-span-" + collection
        canary = "LEGACY_SPAN_" + collection.upper() + "_CANARY"
        timestamp = server.now()
        with server.connect() as conn:
            # Isolate story evidence from the independently tested state path.
            conn.execute("DELETE FROM character_state "
                         "WHERE source_assertion_id IS NOT NULL")
            actual_chapter = conn.execute(
                "SELECT document_node_id FROM source_span WHERE id=?",
                (span_id,)).fetchone()[0]
            if collection == "relationships":
                other = conn.execute(
                    "SELECT id FROM entity WHERE project_id=? AND id<>? LIMIT 1",
                    (seed.PROJECT, self.fixture.entity_id)).fetchone()[0]
                conn.execute(
                    "INSERT INTO app_story_relation(id,project_id,branch_id,"
                    "from_entity_id,to_entity_id,relation_type,note,status,"
                    "created_at,updated_at,evidence_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, seed.PROJECT, seed.BRANCH, self.fixture.entity_id,
                     other, "synthetic legacy relation", canary, "active",
                     timestamp, timestamp, server.json_text(evidence)))
            else:
                self.assertEqual(collection, "timeline")
                conn.execute(
                    "INSERT INTO app_timeline_event(id,project_id,branch_id,"
                    "title,event_time,chapter_id,entity_ids_json,summary,status,"
                    "ordinal,created_at,updated_at,evidence_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, seed.PROJECT, seed.BRANCH, "Synthetic legacy event",
                     "", None, server.json_text([self.fixture.entity_id]), canary,
                     "occurred", 0, timestamp, timestamp, server.json_text(evidence)))
        return identifier, canary, actual_chapter

    def observe_story(self, scenario, collection, seeded, *, stale, consumed,
                      current_chapter="retained-current-chapter",
                      author_plan=False, deleted=False):
        identifier, canary, actual_chapter = seeded
        table = {"relationships": "app_story_relation",
                 "timeline": "app_timeline_event"}[collection]
        before = database_snapshot()
        with server.connect() as conn:
            physical = dict(conn.execute(
                f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone())
            span = dict(conn.execute(
                "SELECT * FROM source_span WHERE document_node_id=? LIMIT 1",
                (actual_chapter,)).fetchone())
        state_status, state = self.call("GET", "/api/state")
        story_status, story = self.call("GET", "/api/story-map")
        self.assertEqual(state_status, 200, state)
        self.assertEqual(story_status, 200, story)
        public = next(item for item in story[collection] if item["id"] == identifier)
        preview_status, preview = self.call("POST", "/api/ai/prompt-preview", {
            "task": "continue", "chapter_id": current_chapter,
            "instruction": "Continue the synthetic span-only evidence control",
            "retrieval_profile": {"chapter_top_k": 0, "recent_window": 0,
                                  "memory_top_k": 0, "record_top_k": 0},
        })
        after = database_snapshot()
        material = None if preview_status != 200 else json.loads(
            preview["user"].split("<manuscript>\n", 1)[1]
            .split("\n</manuscript>", 1)[0])
        selected = None if material is None else identifier in {
            item["id"] for item in material["story_structure"][collection]}
        observation = {
            "test": self._testMethodName, "scenario": scenario,
            "collection": collection, "state_status": state_status,
            "story_map_status": story_status, "expected_stale": stale,
            "public_stale": public["stale"], "public_is_plan": public["is_plan"],
            "public_source_chapter_is_actual":
                public.get("source_chapter_id") == actual_chapter,
            "public_raw_evidence_preserved":
                public["evidence"] == json.loads(physical["evidence_json"]),
            "physical_span_status": span["status"],
            "source_chapter_deleted": deleted,
            "preview_status": preview_status, "preview_error": preview.get("error"),
            "expected_consumed": consumed, "material_consumed": selected,
            "canary_in_prompt": canary in preview.get("user", ""),
            "compared_tables": len(before["tables"]),
            "all_tables_and_schema_unchanged": before == after,
            "database_sha256_before": database_digest(before),
            "database_sha256_after": database_digest(after),
        }
        REPORT["observations"].append(observation)
        with self.subTest(check="span_story_read_preserves_every_table_and_schema"):
            self.assertEqual(set(before["tables"]), EXPECTED_TABLES)
            self.assertEqual(before, after)
            self.assertIn("app_search_fts_data", before["tables"])
            self.assertIn("app_search_fts_idx", before["tables"])
        with self.subTest(check="span_story_physical_history_is_preserved"):
            with server.connect() as conn:
                self.assertEqual(dict(conn.execute(
                    f"SELECT * FROM {table} WHERE id=?",
                    (identifier,)).fetchone()), physical)
                self.assertEqual(dict(conn.execute(
                    "SELECT * FROM source_span WHERE id=?",
                    (span["id"],)).fetchone()), span)
                self.assertEqual(bool(conn.execute(
                    "SELECT 1 FROM app_deleted_node WHERE chapter_id=?",
                    (actual_chapter,)).fetchone()), deleted)
            self.assertEqual(span["status"], "valid")
            self.assertEqual(public["evidence"], json.loads(physical["evidence_json"]))
        with self.subTest(check="span_story_has_derived_actual_source"):
            self.assertEqual(public["stale"], stale)
            self.assertEqual(public["is_plan"], author_plan)
            if author_plan:
                self.assertEqual(public["evidence_status"], "unverified")
                self.assertNotIn("source_chapter_id", public)
            else:
                self.assertEqual(public.get("source_chapter_id"), actual_chapter)
                self.assertEqual(public["source_span_status"],
                                 "stale" if stale else "valid")
                self.assertEqual(public["evidence_status"],
                                 "stale" if stale else "valid")
        with self.subTest(check="span_story_prompt_is_available"):
            self.assertEqual(preview_status, 200, preview)
        if material is not None:
            with self.subTest(check="span_story_prompt_respects_actual_source_scope"):
                self.assertEqual(selected, consumed)
                self.assertEqual(canary in preview["user"], consumed)
                self.assertIn(seed.AUTHOR_CANARY, preview["user"])

    def prepare_assertion_scope(self, kind, *, mixed=False, reverse=False,
                                notes=False, same_ordinal=False, earlier=False):
        canary = "ASSERTION_SCOPE_" + kind.upper() + "_ONLY_CANARY"
        with server.connect() as conn:
            if same_ordinal:
                conn.execute(
                    "UPDATE document_node SET ordinal=1 WHERE project_id=? "
                    "AND branch_id=? AND kind='chapter'",
                    (seed.PROJECT, seed.BRANCH))
            sources = conn.execute(
                "SELECT cm.id memory_id,cm.payload_json,ss.id span_id,"
                "ss.document_node_id chapter_id,n.ordinal "
                "FROM candidate_memory cm JOIN source_span ss ON ss.id=cm.source_span_id "
                "JOIN document_node n ON n.id=ss.document_node_id "
                "WHERE cm.project_id=? AND cm.branch_id=? ORDER BY n.ordinal,n.id",
                (seed.PROJECT, seed.BRANCH)).fetchall()
            self.assertEqual(len(sources), 2)
            source = sources[0] if mixed or earlier else sources[-1]
            assertion_id = self.fixture.assertion_for(conn, source["memory_id"])
            evidence = json.loads(conn.execute(
                "SELECT evidence_json FROM assertion WHERE id=?",
                (assertion_id,)).fetchone()[0])
            if mixed:
                evidence.append({"source_span_id": sources[-1]["span_id"]})
            if reverse:
                evidence.reverse()
            if notes:
                evidence = ["Synthetic scope note", *evidence,
                            {"note": "Synthetic scope note object"}]
            conn.execute("UPDATE assertion SET evidence_json=? WHERE id=?",
                         (server.json_text(evidence), assertion_id))
            conn.execute("DELETE FROM character_state WHERE source_assertion_id "
                         "IS NOT NULL AND id<>'character-delete-review'")
            if kind == "character":
                conn.execute(
                    "UPDATE character_state SET source_assertion_id=?,value_json=? "
                    "WHERE id='character-delete-review'",
                    (assertion_id, server.json_text({"item": canary})))
            else:
                self.assertEqual(kind, "conflict")
                payload = json.loads(source["payload_json"])
                payload["name"] = canary
                conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id=?",
                             (server.json_text(payload), source["memory_id"]))
                conn.execute("DELETE FROM character_state "
                             "WHERE source_assertion_id IS NOT NULL")
            memory_key = json.loads(source["payload_json"])["key"]
        return memory_key, canary

    def assertion_scope_physical(self, project, branch, memory_key):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT cm.id memory_id,cm.status memory_status,cm.payload_json,"
                "cm.confidence memory_confidence,cm.reviewed_at memory_reviewed_at,"
                "cm.reviewed_by memory_reviewed_by,"
                "ma.kind memory_actor_kind,ma.name memory_actor_name,"
                "ma.project_id memory_actor_project,"
                "a.id assertion_id,a.state assertion_state,a.evidence_json,"
                "a.object_json,a.confidence assertion_confidence,"
                "a.reviewed_at assertion_reviewed_at,a.reviewed_by assertion_reviewed_by,"
                "aa.kind assertion_actor_kind,aa.name assertion_actor_name,"
                "aa.project_id assertion_actor_project "
                "FROM candidate_memory cm JOIN assertion a ON a.project_id=cm.project_id "
                "AND a.branch_id=cm.branch_id JOIN json_each(a.evidence_json) e "
                "ON CASE WHEN e.type='object' THEN "
                "json_extract(e.value,'$.candidate_memory_id') END=cm.id "
                "LEFT JOIN actor ma ON ma.id=cm.reviewed_by "
                "LEFT JOIN actor aa ON aa.id=a.reviewed_by "
                "WHERE cm.project_id=? AND cm.branch_id=? "
                "AND json_extract(cm.payload_json,'$.key')=?",
                (project, branch, memory_key)).fetchone()
            self.assertIsNotNone(row)
            result = dict(row)
            normalized_evidence = []
            actual_sources = []
            for raw in json.loads(row["evidence_json"]):
                if not isinstance(raw, dict):
                    normalized_evidence.append(raw)
                    continue
                entry = {key: value for key, value in raw.items()
                         if key not in {"candidate_memory_id", "source_span_id"}}
                if "candidate_memory_id" in raw:
                    memory = conn.execute(
                        "SELECT payload_json FROM candidate_memory WHERE id=? "
                        "AND project_id=? AND branch_id=?",
                        (raw["candidate_memory_id"], project, branch)).fetchone()
                    self.assertIsNotNone(memory)
                    entry["candidate_memory_key"] = json.loads(memory[0])["key"]
                if "source_span_id" in raw:
                    source = conn.execute(
                        "SELECT ss.quote,ss.quote_hash,ss.status,ss.document_node_id,"
                        "n.title,n.ordinal,r.content_hash "
                        "FROM source_span ss JOIN document_node n ON n.id=ss.document_node_id "
                        "JOIN document_revision r ON r.id=ss.revision_id "
                        "WHERE ss.id=? AND ss.project_id=? AND n.project_id=? "
                        "AND n.branch_id=?",
                        (raw["source_span_id"], project, project, branch)).fetchone()
                    self.assertIsNotNone(source)
                    self.assertEqual(source["status"], "valid")
                    actual_sources.append((source["ordinal"], source["document_node_id"]))
                    entry["source"] = {key: source[key] for key in
                                       ("quote", "quote_hash", "status", "title",
                                        "ordinal", "content_hash")}
                normalized_evidence.append(entry)
            self.assertTrue(actual_sources)
            result["actual_source_chapter_id"] = max(actual_sources)[1]
            payload = json.loads(row["payload_json"])
            result["normalized"] = {
                key: row[key] for key in (
                    "memory_status", "memory_confidence", "memory_reviewed_at",
                    "memory_actor_kind", "memory_actor_name", "assertion_state",
                    "assertion_confidence", "assertion_reviewed_at",
                    "assertion_actor_kind", "assertion_actor_name")}
            result["normalized"].update({
                "memory_payload": {key: payload.get(key) for key in (
                    "key", "name", "source", "note", "source_quote",
                    "source_start", "source_end", "source_hash")},
                "assertion_object": json.loads(row["object_json"]),
                "evidence": normalized_evidence,
            })
            return result

    def observe_assertion_scope(self, kind, *, mixed=False, reverse=False,
                                notes=False, same_ordinal=False, earlier=False,
                                restored=False):
        memory_key, canary = self.prepare_assertion_scope(
            kind, mixed=mixed, reverse=reverse, notes=notes,
            same_ordinal=same_ordinal, earlier=earlier)
        project, branch = seed.PROJECT, seed.BRANCH
        original = self.assertion_scope_physical(project, branch, memory_key)
        if restored:
            self.assertFalse(same_ordinal)
            project, branch = self.restore_zip()
        physical = self.assertion_scope_physical(project, branch, memory_key)
        if restored:
            authors_mapped = all(physical[key] == project for key in
                                 ("memory_actor_project", "assertion_actor_project"))
            REPORT["scope_restore_integrity"].append({
                "test": self._testMethodName,
                "target_author_history_and_actual_evidence_preserved":
                    original["normalized"] == physical["normalized"],
                "target_review_authors_remapped_to_restored_project": authors_mapped,
                "target_memory_identity_remapped":
                    original["memory_id"] != physical["memory_id"],
                "target_assertion_identity_remapped":
                    original["assertion_id"] != physical["assertion_id"],
                "target_actual_source_chapter_remapped":
                    original["actual_source_chapter_id"] !=
                    physical["actual_source_chapter_id"],
            })
            with self.subTest(check="scope_zip_preserves_target_evidence_and_review"):
                self.assertEqual(original["normalized"], physical["normalized"])
                self.assertTrue(authors_mapped)
                for key in ("memory_id", "assertion_id", "actual_source_chapter_id"):
                    self.assertNotEqual(original[key], physical[key])
        before = database_snapshot()
        state_status, state = self.call("GET", "/api/state",
                                        project=project, branch=branch)
        chars_status, chars = self.call("GET", "/api/character-states",
                                        project=project, branch=branch)
        story_status, story = self.call("GET", "/api/story-map",
                                        project=project, branch=branch)
        assertions_status, assertions = self.call(
            "GET", "/api/assertions?state=canon", project=project, branch=branch)
        for status, result in ((state_status, state), (chars_status, chars),
                               (story_status, story),
                               (assertions_status, assertions)):
            self.assertEqual(status, 200, result)
        chapter_ids = [item["id"] for item in state["chapters"]]
        self.assertEqual(len(chapter_ids), 2)
        self.assertIn(physical["actual_source_chapter_id"], chapter_ids)
        collection = "character_states" if kind == "character" else "conflicts"
        target_id = physical["assertion_id"]
        if kind == "character":
            public = next(item for item in chars["character_states"]
                          if item["attribute"] == "character-delete-review")
            target_id = public["id"]
        else:
            public = next(item for item in story["conflicts"] if item["id"] == target_id)
        assertion = next(item for item in assertions["assertions"]
                         if item["id"] == physical["assertion_id"])
        author = next(item for item in chars["character_states"]
                      if item["attribute"] == "character-author-review")
        previews = []
        for label, current in (("early", chapter_ids[0]), ("late", chapter_ids[-1])):
            expected_consumed = (
                chapter_ids.index(physical["actual_source_chapter_id"]) <=
                chapter_ids.index(current))
            status, preview = self.call("POST", "/api/ai/prompt-preview", {
                "task": "continue", "chapter_id": current,
                "instruction": "Continue the synthetic assertion scope control",
                "retrieval_profile": {"chapter_top_k": 0, "recent_window": 0,
                                      "memory_top_k": 0, "record_top_k": 0},
            }, project=project, branch=branch)
            material = None if status != 200 else json.loads(
                preview["user"].split("<manuscript>\n", 1)[1]
                .split("\n</manuscript>", 1)[0])
            consumed = None if material is None else target_id in {
                item["id"] for item in material["story_structure"][collection]}
            previews.append({
                "chapter_position": label, "status": status,
                "error": preview.get("error"), "expected_consumed": expected_consumed,
                "material_consumed": consumed,
                "canary_in_prompt": canary in preview.get("user", ""),
                "confirmed_memory_empty":
                    material is not None and not material["confirmed_memory"],
            })
            with self.subTest(check="assertion_scope_preview", chapter=label):
                self.assertEqual(status, 200, preview)
            if material is not None:
                with self.subTest(check="assertion_scope_consumption", chapter=label):
                    self.assertEqual(consumed, expected_consumed)
                    self.assertEqual(canary in preview["user"], expected_consumed)
                    self.assertEqual(material["confirmed_memory"], [])
                    self.assertIn(seed.AUTHOR_CANARY, preview["user"])
        after = database_snapshot()
        REPORT["observations"].append({
            "test": self._testMethodName, "scenario": "assertion_scope_" + kind,
            "restored": restored, "mixed_spans": mixed,
            "evidence_order_reversed": reverse, "mixed_notes": notes,
            "same_chapter_ordinal": same_ordinal, "earlier_source_control": earlier,
            "public_material_stale": public["stale"],
            "public_source_chapter_matches_actual_latest":
                public.get("source_chapter_id") == physical["actual_source_chapter_id"],
            "assertion_stale": assertion["stale"], "previews": previews,
            "compared_tables": len(before["tables"]),
            "all_tables_and_schema_unchanged": before == after,
            "database_sha256_before": database_digest(before),
            "database_sha256_after": database_digest(after),
        })
        with self.subTest(check="assertion_scope_preserves_all_tables_and_history"):
            self.assertEqual(set(before["tables"]), EXPECTED_TABLES)
            self.assertEqual(before, after)
            self.assertEqual(
                self.assertion_scope_physical(project, branch, memory_key), physical)
            self.assertEqual(physical["memory_status"], "accepted")
            self.assertEqual(physical["assertion_state"], "canon")
            self.assertIn("app_search_fts_data", before["tables"])
            self.assertIn("app_search_fts_idx", before["tables"])
        with self.subTest(check="assertion_scope_uses_latest_actual_chapter"):
            self.assertFalse(public["stale"])
            self.assertEqual(assertion["stale"], notes)
            self.assertEqual(
                public.get("source_chapter_id"), physical["actual_source_chapter_id"])
        with self.subTest(check="unlinked_author_has_no_invented_source"):
            self.assertIsNone(author["source_assertion_id"])
            self.assertFalse(author["stale"])
            self.assertNotIn("source_chapter_id", author)

    def observe_nonchapter_assertion(self, kind, *, mixed=False):
        canary = "NONCHAPTER_" + kind.upper() + "_ONLY_CANARY"
        memory_key = "synthetic-nonchapter-review"
        quote = "Synthetic nonchapter source quote."
        with server.connect() as conn:
            server.create_chapter(conn, {
                "id": "synthetic-nonchapter-node",
                "title": "Synthetic nonchapter source", "body": quote,
            })
            span_id = server.source_span_for_memory(conn, {
                "source_chapter_id": "synthetic-nonchapter-node",
                "source_quote": quote, "source_start": 0, "source_end": len(quote),
            })
            self.assertTrue(span_id)
            payload = json.loads(conn.execute(
                "SELECT payload_json FROM candidate_memory "
                "WHERE id='memory-retained-review'").fetchone()[0])
            payload.update({
                "key": memory_key, "note": quote,
                "source_chapter_id": "synthetic-nonchapter-node",
                "source_quote": quote, "source_start": 0, "source_end": len(quote),
                "source_hash": server.hash_text(quote),
            })
            if kind == "conflict":
                payload["name"] = canary
            evidence = [{
                "candidate_memory_id": memory_key, "source_span_id": span_id,
            }]
            if mixed:
                early_span = conn.execute(
                    "SELECT source_span_id FROM candidate_memory "
                    "WHERE id='memory-delete-review'").fetchone()[0]
                evidence.insert(0, {"source_span_id": early_span})
            for table, source_id, changes in (
                    ("candidate_memory", "memory-retained-review", {
                        "id": memory_key, "source_span_id": span_id,
                        "payload_json": server.json_text(payload)}),
                    ("assertion", self.fixture.keep_assertion_id, {
                        "id": "synthetic-nonchapter-assertion",
                        "evidence_json": server.json_text(evidence)})):
                row = dict(conn.execute(
                    f"SELECT * FROM {table} WHERE id=?", (source_id,)).fetchone())
                row.update(changes)
                columns = ",".join(row)
                placeholders = ",".join("?" for _key in row)
                conn.execute(f"INSERT INTO {table}({columns}) VALUES({placeholders})",
                             tuple(row.values()))
            conn.execute(
                "UPDATE document_node SET kind='volume' "
                "WHERE id='synthetic-nonchapter-node'")
            conn.execute("DELETE FROM character_state WHERE source_assertion_id "
                         "IS NOT NULL AND id<>'character-delete-review'")
            if kind == "character":
                conn.execute(
                    "UPDATE character_state SET source_assertion_id=?,value_json=? "
                    "WHERE id='character-delete-review'",
                    ("synthetic-nonchapter-assertion", server.json_text({"item": canary})))
            else:
                self.assertEqual(kind, "conflict")
                conn.execute("DELETE FROM character_state "
                             "WHERE source_assertion_id IS NOT NULL")
        physical = self.assertion_scope_physical(seed.PROJECT, seed.BRANCH, memory_key)
        before = database_snapshot()
        state_status, state = self.call("GET", "/api/state")
        chars_status, chars = self.call("GET", "/api/character-states")
        story_status, story = self.call("GET", "/api/story-map")
        assertions_status, assertions = self.call("GET", "/api/assertions?state=canon")
        for status, result in ((state_status, state), (chars_status, chars),
                               (story_status, story),
                               (assertions_status, assertions)):
            self.assertEqual(status, 200, result)
        self.assertEqual(len(state["chapters"]), 2)
        self.assertNotIn("synthetic-nonchapter-node", {
            item["id"] for item in state["chapters"]})
        collection = "character_states" if kind == "character" else "conflicts"
        public = next(item for item in story[collection]
                      if (item.get("attribute") == "character-delete-review"
                          if kind == "character"
                          else item["id"] == physical["assertion_id"]))
        assertion = next(item for item in assertions["assertions"]
                         if item["id"] == physical["assertion_id"])
        status, preview = self.call("POST", "/api/ai/prompt-preview", {
            "task": "continue", "chapter_id": "retained-current-chapter",
            "instruction": "Continue the synthetic nonchapter source control",
            "retrieval_profile": {"chapter_top_k": 0, "recent_window": 0,
                                  "memory_top_k": 0, "record_top_k": 0},
        })
        after = database_snapshot()
        material = None if status != 200 else json.loads(
            preview["user"].split("<manuscript>\n", 1)[1]
            .split("\n</manuscript>", 1)[0])
        consumed = None if material is None else public["id"] in {
            item["id"] for item in material["story_structure"][collection]}
        REPORT["observations"].append({
            "test": self._testMethodName, "scenario": "nonchapter_assertion_" + kind,
            "mixed_valid_chapter_source": mixed,
            "public_material_stale": public["stale"],
            "assertion_stale": assertion["stale"],
            "preview_status": status, "preview_error": preview.get("error"),
            "material_consumed": consumed,
            "canary_in_prompt": canary in preview.get("user", ""),
            "compared_tables": len(before["tables"]),
            "all_tables_and_schema_unchanged": before == after,
            "database_sha256_before": database_digest(before),
            "database_sha256_after": database_digest(after),
        })
        with self.subTest(check="nonchapter_history_and_tables_are_read_only"):
            self.assertEqual(set(before["tables"]), EXPECTED_TABLES)
            self.assertEqual(before, after)
            self.assertEqual(
                self.assertion_scope_physical(seed.PROJECT, seed.BRANCH, memory_key),
                physical)
            self.assertEqual(physical["memory_status"], "accepted")
            self.assertEqual(physical["assertion_state"], "canon")
            self.assertIn("app_search_fts_data", before["tables"])
            self.assertIn("app_search_fts_idx", before["tables"])
            with server.connect() as conn:
                self.assertEqual(conn.execute(
                    "SELECT kind FROM document_node WHERE id="
                    "'synthetic-nonchapter-node'").fetchone()[0], "volume")
        with self.subTest(check="nonchapter_material_is_derived_stale"):
            self.assertFalse(assertion["stale"])
            self.assertTrue(public["stale"])
        with self.subTest(check="nonchapter_source_does_not_block_new_preview"):
            self.assertEqual(status, 200, preview)
        if material is not None:
            with self.subTest(check="nonchapter_source_is_not_consumed"):
                self.assertFalse(consumed)
                self.assertNotIn(canary, preview["user"])
                self.assertEqual(material["confirmed_memory"], [])
                self.assertIn(seed.AUTHOR_CANARY, preview["user"])
        with self.subTest(check="nonchapter_unlinked_author_remains_available"):
            author = next(item for item in chars["character_states"]
                          if item["attribute"] == "character-author-review")
            self.assertFalse(author["stale"])
            self.assertIsNone(author["source_assertion_id"])
            self.assertNotIn("source_chapter_id", author)

    def legacy_case(self, *, restored=False, conflict=False):
        if conflict:
            self.set_alias_conflict()
        self.legacy_delete()
        scope = self.restore_zip() if restored else (seed.PROJECT, seed.BRANCH)
        self.observe(
            ("restored" if restored else "direct") + (
                "_legacy_deleted_alias_conflict" if conflict
                else "_legacy_deleted_character"),
            project=scope[0], branch=scope[1], stale=True,
            conflict=conflict, deleted=True)

    def test_direct_legacy_deleted_character_is_excluded(self):
        self.legacy_case()

    def test_restored_legacy_deleted_character_is_excluded(self):
        self.legacy_case(restored=True)

    def test_direct_legacy_deleted_alias_conflict_is_excluded(self):
        self.legacy_case(conflict=True)

    def test_restored_legacy_deleted_alias_conflict_is_excluded(self):
        self.legacy_case(restored=True, conflict=True)

    def test_older_revision_character_is_derived_stale(self):
        with server.connect() as conn:
            identifier = server.uid("rev_")
            body = bytes(self.fixture.source_revision[2]).decode("utf-8") + "\nLater revision."
            conn.execute(
                "INSERT INTO document_revision(id,document_node_id,parent_id,branch_id,"
                "content_hash,canonical_text,created_at,actor_id,operation) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (identifier, self.fixture.chapter_id, self.fixture.source_revision[0],
                 seed.BRANCH, server.hash_text(body), body.encode("utf-8"),
                 server.now(), seed.ACTOR, "update"))
            conn.execute("UPDATE branch SET head_revision=? WHERE id=?",
                         (identifier, seed.BRANCH))
        self.observe("older_revision_character", stale=True)

    def test_stale_span_character_is_derived_stale(self):
        with server.connect() as conn:
            conn.execute(
                "UPDATE source_span SET status='stale' WHERE id="
                "(SELECT source_span_id FROM candidate_memory "
                "WHERE id='memory-delete-review')")
        self.observe("stale_span_character", stale=True, span_status="stale")

    def test_withdrawn_memory_character_is_derived_stale(self):
        with server.connect() as conn:
            conn.execute("UPDATE candidate_memory SET status='rejected' "
                         "WHERE id='memory-delete-review'")
        self.observe("withdrawn_memory_character", stale=True,
                     memory_status="rejected", memory_stale=False)

    def test_valid_linked_and_unlinked_author_states_remain_usable(self):
        self.observe("valid_linked_and_unlinked_author_states", stale=False)

    def test_valid_alias_conflict_preserves_historical_labels(self):
        self.set_alias_conflict()
        observation = self.observe(
            "valid_alias_conflict", stale=False, conflict=True)
        self.assertEqual(observation["memory_name"], "historical-author-label")
        self.assertEqual(observation["physical"]["confidence"], 0.75)

    def test_valid_linked_state_with_mixed_notes_remains_usable(self):
        self.append_assertion_evidence(
            "Synthetic author note without a source reference",
            {"note": "Synthetic supporting note"})
        self.observe("valid_character_mixed_notes", stale=False,
                     assertion_stale=True)

    def test_valid_alias_conflict_with_mixed_notes_remains_usable(self):
        self.set_alias_conflict()
        self.append_assertion_evidence(
            "Synthetic author note without a source reference",
            {"note": "Synthetic supporting note"})
        self.observe("valid_alias_conflict_mixed_notes", stale=False,
                     assertion_stale=True, conflict=True)

    def test_explicit_empty_span_reference_excludes_linked_state(self):
        self.append_assertion_evidence({"source_span_id": ""})
        self.observe("explicit_empty_span_reference", stale=True,
                     memory_stale=False, memory_consumed=True)

    def test_explicit_null_span_reference_excludes_linked_state(self):
        self.append_assertion_evidence({"source_span_id": None})
        self.observe("explicit_null_span_reference", stale=True,
                     memory_stale=False, memory_consumed=True)

    def test_explicit_empty_memory_with_valid_span_excludes_linked_state(self):
        self.append_assertion_evidence({
            "candidate_memory_id": "", "source_span_id": self.source_span_id()})
        self.observe("explicit_empty_memory_with_valid_span", stale=True,
                     assertion_stale=False, memory_stale=False,
                     memory_consumed=True)

    def test_explicit_null_memory_with_valid_span_excludes_linked_state(self):
        self.append_assertion_evidence({
            "candidate_memory_id": None, "source_span_id": self.source_span_id()})
        self.observe("explicit_null_memory_with_valid_span", stale=True,
                     assertion_stale=False, memory_stale=False,
                     memory_consumed=True)

    def test_explicit_null_memory_with_valid_span_excludes_alias_conflict(self):
        self.set_alias_conflict()
        self.append_assertion_evidence({
            "candidate_memory_id": None, "source_span_id": self.source_span_id()})
        self.observe("alias_conflict_explicit_null_memory", stale=True,
                     assertion_stale=False, memory_stale=False,
                     memory_consumed=True, conflict=True)

    def test_missing_span_reference_excludes_linked_state(self):
        self.append_assertion_evidence({
            "source_span_id": "synthetic-nonexistent-source-span"})
        self.observe("missing_span_reference", stale=True,
                     memory_stale=False, memory_consumed=True)

    def test_missing_memory_with_valid_span_excludes_linked_state(self):
        self.append_assertion_evidence({
            "candidate_memory_id": "synthetic-nonexistent-candidate-memory",
            "source_span_id": self.source_span_id()})
        self.observe("missing_memory_with_valid_span", stale=True,
                     memory_stale=False, memory_consumed=True)

    def test_missing_span_reference_excludes_alias_conflict(self):
        self.set_alias_conflict()
        self.append_assertion_evidence({
            "source_span_id": "synthetic-nonexistent-source-span"})
        self.observe("alias_conflict_missing_span_reference", stale=True,
                     memory_stale=False, memory_consumed=True, conflict=True)

    def test_span_only_deleted_relation_is_derived_stale_and_excluded(self):
        seeded = self.create_span_story("relationships")
        self.legacy_delete()
        self.observe_story("span_only_deleted_relation", "relationships", seeded,
                           stale=True, consumed=False, deleted=True)

    def test_span_only_deleted_timeline_is_derived_stale_and_excluded(self):
        seeded = self.create_span_story("timeline")
        self.legacy_delete()
        self.observe_story("span_only_deleted_timeline", "timeline", seeded,
                           stale=True, consumed=False, deleted=True)

    def test_span_only_earlier_relation_remains_usable(self):
        seeded = self.create_span_story("relationships")
        self.observe_story("span_only_earlier_relation", "relationships", seeded,
                           stale=False, consumed=True)

    def test_span_only_earlier_timeline_remains_usable(self):
        seeded = self.create_span_story("timeline")
        self.observe_story("span_only_earlier_timeline", "timeline", seeded,
                           stale=False, consumed=True)

    def test_span_only_future_relation_is_excluded_without_becoming_stale(self):
        seeded = self.create_span_story(
            "relationships", memory_id="memory-retained-review")
        self.observe_story("span_only_future_relation", "relationships", seeded,
                           stale=False, consumed=False,
                           current_chapter=self.fixture.chapter_id)

    def test_span_only_future_timeline_is_excluded_without_becoming_stale(self):
        seeded = self.create_span_story("timeline", memory_id="memory-retained-review")
        self.observe_story("span_only_future_timeline", "timeline", seeded,
                           stale=False, consumed=False,
                           current_chapter=self.fixture.chapter_id)

    def test_declared_earlier_relation_cannot_mask_actual_future_source(self):
        seeded = self.create_span_story(
            "relationships", memory_id="memory-retained-review",
            declared_chapter=self.fixture.chapter_id)
        self.observe_story("declared_earlier_actual_future_relation",
                           "relationships", seeded, stale=True, consumed=False,
                           current_chapter=self.fixture.chapter_id)

    def test_declared_earlier_timeline_cannot_mask_actual_future_source(self):
        seeded = self.create_span_story(
            "timeline", memory_id="memory-retained-review",
            declared_chapter=self.fixture.chapter_id)
        self.observe_story("declared_earlier_actual_future_timeline",
                           "timeline", seeded, stale=True, consumed=False,
                           current_chapter=self.fixture.chapter_id)

    def test_declared_deleted_relation_with_valid_actual_source_is_excluded(self):
        seeded = self.create_span_story(
            "relationships", memory_id="memory-retained-review",
            declared_chapter=self.fixture.chapter_id)
        self.legacy_delete()
        self.observe_story("declared_deleted_actual_current_relation",
                           "relationships", seeded, stale=True, consumed=False)

    def test_unreferenced_manual_relation_preserves_author_plan_behavior(self):
        seeded = self.create_span_story("relationships", author_plan=True)
        self.observe_story("manual_relation_without_reference", "relationships",
                           seeded, stale=False, consumed=False, author_plan=True)

    def test_future_assertion_character_respects_early_and_late_chapters(self):
        self.observe_assertion_scope("character")

    def test_future_assertion_conflict_respects_early_and_late_chapters(self):
        self.observe_assertion_scope("conflict")

    def test_earlier_assertion_character_remains_usable_in_both_chapters(self):
        self.observe_assertion_scope("character", earlier=True)

    def test_earlier_assertion_conflict_remains_usable_in_both_chapters(self):
        self.observe_assertion_scope("conflict", earlier=True)

    def test_future_assertion_character_with_notes_respects_scope(self):
        self.observe_assertion_scope("character", notes=True)

    def test_future_assertion_conflict_with_notes_respects_scope(self):
        self.observe_assertion_scope("conflict", notes=True)

    def test_mixed_span_character_uses_latest_source_in_forward_order(self):
        self.observe_assertion_scope("character", mixed=True)

    def test_mixed_span_character_uses_latest_source_in_reverse_order(self):
        self.observe_assertion_scope("character", mixed=True, reverse=True)

    def test_mixed_span_conflict_uses_latest_source_in_forward_order(self):
        self.observe_assertion_scope("conflict", mixed=True)

    def test_mixed_span_conflict_uses_latest_source_in_reverse_order(self):
        self.observe_assertion_scope("conflict", mixed=True, reverse=True)

    def test_same_ordinal_character_uses_public_chapter_identity_order(self):
        self.observe_assertion_scope("character", same_ordinal=True)

    def test_same_ordinal_conflict_uses_public_chapter_identity_order(self):
        self.observe_assertion_scope("conflict", same_ordinal=True)

    def test_same_ordinal_mixed_span_character_uses_latest_identity_forward(self):
        self.observe_assertion_scope("character", same_ordinal=True, mixed=True)

    def test_same_ordinal_mixed_span_conflict_uses_latest_identity_reverse(self):
        self.observe_assertion_scope(
            "conflict", same_ordinal=True, mixed=True, reverse=True)

    def test_restored_future_assertion_character_preserves_scope_and_history(self):
        self.observe_assertion_scope("character", restored=True)

    def test_restored_future_assertion_conflict_preserves_scope_and_history(self):
        self.observe_assertion_scope("conflict", restored=True)

    def test_nonchapter_source_character_is_derived_stale_and_excluded(self):
        self.observe_nonchapter_assertion("character")

    def test_nonchapter_source_conflict_is_derived_stale_and_excluded(self):
        self.observe_nonchapter_assertion("conflict")

    def test_mixed_nonchapter_source_character_is_derived_stale_and_excluded(self):
        self.observe_nonchapter_assertion("character", mixed=True)

    def test_mixed_nonchapter_source_conflict_is_derived_stale_and_excluded(self):
        self.observe_nonchapter_assertion("conflict", mixed=True)


def main():
    output = os.environ.get("MOYU_LEGACY_DELETED_DEPENDENCY_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; choose a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        LegacyDeletedDependenciesHTTP)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful(), "methods": result.testsRun,
        "failures": [{"test": str(test), "traceback": trace}
                     for test, trace in result.failures],
        "errors": [{"test": str(test), "traceback": trace}
                   for test, trace in result.errors],
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after, "paid_provider_calls": 0,
        "production_database_accessed": False, "credential_files_read": False,
        "raw_database_values_saved": False,
        "scope": "temporary synthetic SQLite; real loopback HTTP; no provider",
        "claim_limit": "legacy evidence read integrity; no literary-quality claim",
        **copy.deepcopy(REPORT),
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"http", "observations", "failures", "errors"}},
                     ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
