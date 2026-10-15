#!/usr/bin/env python3
"""Independent complete-ZIP boundary and recovery fixtures.

Only temporary synthetic databases are opened. No provider or worker may run.
The row oracle maps declared identities and named JSON paths; it never maps
arbitrary strings merely because their text happens to equal an old identity.
"""
from __future__ import annotations

import base64
import copy
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_analysis as analysis  # noqa: E402
import moyu_backups as backups  # noqa: E402
import moyu_import as manuscript_import  # noqa: E402
import moyu_planning as planning  # noqa: E402
import moyu_records as records  # noqa: E402
import moyu_workspace as workspace  # noqa: E402
import server  # noqa: E402


BASE_TABLES = {
    "project", "branch", "document_node", "document_revision", "event_log",
    "deletion_event", "source_span", "entity", "candidate_memory", "ai_run",
    "merge_conflict", "audit_event", "actor", "assertion", "character_state",
    "plot_thread", "external_source", "prov_entity", "prov_activity",
    "prov_agent", "prov_edge", "export_job", "run_manifest", "app_project_meta",
    "app_state_version", "app_entity_meta", "app_chapter_meta",
    "app_deleted_node", "app_plot_item", "app_ai_run_decision",
    "app_story_relation", "app_timeline_event", "app_foreshadow_link",
    "app_chapter_commit", "app_ai_job", "app_ai_chunk", "app_workspace",
    "app_blueprint", "app_chapter_structure", "app_candidate_application",
    "app_model_settings", "app_quota_reservation", "app_assistant_request",
    "app_chapter_analysis", "app_story_review", "app_manuscript_import",
    "app_reference_document", "app_reference_section", "app_planning_workflow",
    "app_planning_attempt", "app_planning_event", "app_planning_application",
}
AGENT_TABLES = {"app_agent_session", "app_agent_checkpoint", "app_agent_write"}
ADDITIVE_TABLES = {"app_candidate_revision", "app_entity_scope"} | AGENT_TABLES
ALL_TABLES = BASE_TABLES | ADDITIVE_TABLES
SOURCE_PROJECT = "portable-source-book"
SOURCE_BRANCH = "portable-main"
SOURCE_ACTOR = "portable-author"
ALTERNATE_BRANCH = "portable-alternate"
STAMP = "2026-10-15T00:00:00+00:00"
SYNTHETIC_CREDENTIAL = "portable-synthetic-private-value"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text_hash(text: str) -> str:
    return digest(text.encode("utf-8"))


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def insert(conn: sqlite3.Connection, table: str, **row) -> None:
    names = ",".join(f'"{name}"' for name in row)
    placeholders = ",".join("?" for _ in row)
    conn.execute(f'INSERT INTO "{table}" ({names}) VALUES({placeholders})',
                 list(row.values()))


def decoded(value):
    if isinstance(value, dict) and set(value) == {"$blob"}:
        return base64.b64decode(value["$blob"], validate=True)
    return copy.deepcopy(value)


def snapshot(conn: sqlite3.Connection) -> dict:
    """Freeze every table, row order, BLOB, and installed schema object."""
    schema = [tuple(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    )]
    tables = {}
    for table in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name = table[0]
        try:
            rows = conn.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid')
        except sqlite3.OperationalError as error:
            if "no such column: rowid" not in str(error):
                raise
            primary_key = [row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')
                           if row[5]]
            order = ",".join(f'"{field}"' for field in primary_key)
            rows = conn.execute(f'SELECT * FROM "{name}" ORDER BY {order}')
        tables[name] = [tuple(row) for row in rows]
    return {"schema": schema, "tables": tables,
            "user_version": conn.execute("PRAGMA user_version").fetchone()[0]}


def archive_bytes(payload, *, manifest_overrides=None, raw_project=None,
                  raw_manifest=None, duplicate=None, extras=(),
                  member_mode=None, compression=zipfile.ZIP_DEFLATED) -> bytes:
    raw = raw_project if raw_project is not None else json_text(payload).encode("utf-8")
    manifest = {
        "id": "backup_synthetic_portable",
        "project_id": payload.get("project_id", SOURCE_PROJECT),
        "branch_id": next((row["id"] for row in payload["tables"].get("branch", [])
                           if row.get("status") == "active"), SOURCE_BRANCH),
        "project_title": next((row.get("name", "synthetic title")
                               for row in payload["tables"].get("project", [])),
                              "synthetic title"),
        "scope": "complete_project",
        "created_at": STAMP,
        "sha256": digest(raw),
        "verified": True,
        "row_counts": {name: len(rows) for name, rows in payload["tables"].items()},
        "source_integrity": "ok",
        "excluded": ["credentials", "other_projects", "rebuildable_search_index"],
    }
    manifest.update(manifest_overrides or {})
    raw_manifest = (raw_manifest if raw_manifest is not None
                    else json_text(manifest).encode("utf-8"))
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=compression) as archive:
            for name, content in (("project.json", raw), ("manifest.json", raw_manifest)):
                if duplicate == name:
                    archive.writestr(name, b'{"ignored_first_member":true}')
                info = zipfile.ZipInfo(name)
                info.compress_type = compression
                if member_mode is not None:
                    info.create_system = 3
                    info.external_attr = member_mode << 16
                archive.writestr(info, content)
            for name, content in extras:
                archive.writestr(name, content)
    return output.getvalue()


def patch_zip_header(raw: bytes, field: str, value: int) -> bytes:
    """Alter both ZIP headers without changing member contents."""
    changed = bytearray(raw)
    offsets = {"flags": (6, 8), "compression": (8, 10), "size": (22, 24)}
    local_offset, central_offset = offsets[field]
    width = "<I" if field == "size" else "<H"
    for signature, relative in ((b"PK\x03\x04", local_offset),
                                (b"PK\x01\x02", central_offset)):
        position = changed.find(signature)
        if position < 0:
            raise AssertionError("synthetic ZIP header missing")
        struct.pack_into(width, changed, position + relative, value)
    return bytes(changed)


def corrupted_crc(raw: bytes) -> bytes:
    changed = bytearray(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.getinfo("project.json")
        filename_length, extra_length = struct.unpack_from(
            "<HH", raw, info.header_offset + 26
        )
        start = info.header_offset + 30 + filename_length + extra_length
        changed[start + info.compress_size // 2] ^= 1
    return bytes(changed)


def seed_source(conn: sqlite3.Connection) -> dict:
    """Build a real schema with nonempty rows in every captured table."""
    chapter = server.chapter_rows(conn)[0]
    chapter_id = chapter["id"]
    first_body = "\ufeff林岚在雨里握住罗盘。\r\n𠀀与🙂保留原字节。\r\n" + chapter_id
    server.update_chapter(conn, chapter_id, {
        "body": first_body, "title": chapter_id, "scene_plan": chapter_id,
        "chapter_goal": chapter_id, "hook": chapter_id,
    })
    revision = server.chapter_rows(conn)[0]["revision_id"]
    quote_start, quote_end = first_body.index("𠀀"), first_body.index("𠀀") + 6
    quote = first_body[quote_start:quote_end]
    entity_ids = [row[0] for row in conn.execute(
        "SELECT id FROM entity WHERE project_id=? ORDER BY rowid", (SOURCE_PROJECT,)
    )]
    entity_id, second_entity = entity_ids[:2]
    conn.execute("UPDATE entity SET aliases_json=? WHERE id=?",
                 (json_text([chapter_id, "作者文字", "chapter-9"]), entity_id))
    conn.execute("UPDATE app_entity_meta SET description=? WHERE entity_id=?",
                 (chapter_id, entity_id))
    conn.execute("UPDATE app_project_meta SET summary=? WHERE project_id=?",
                 (chapter_id, SOURCE_PROJECT))
    records.write_scope(conn, entity_id, {
        "known_by": [second_entity, "人物名称"],
        "available_from": "chapter-9",
        "knowledge_scope": {"branches": [SOURCE_BRANCH],
                            "project_id": SOURCE_PROJECT,
                            "subject_entity_id": entity_id,
                            "story_time": chapter_id},
    }, STAMP)
    insert(conn, "source_span", id="portable-span", project_id=SOURCE_PROJECT,
           revision_id=revision, document_node_id=chapter_id,
           start_codepoint=quote_start, end_codepoint=quote_end,
           start_utf8=len(first_body[:quote_start].encode("utf-8")),
           end_utf8=len(first_body[:quote_end].encode("utf-8")),
           quote_hash=text_hash(quote), quote=quote, anchor_before="",
           anchor_after="", status="valid", external_uri="offline://synthetic-source",
           snapshot_hash=text_hash(quote), snapshot_at=STAMP)
    evidence = {"source_span_id": "portable-span",
                "source_chapter_id": chapter_id, "source_revision_id": revision,
                "source_quote": quote, "source_start": quote_start,
                "source_end": quote_end, "source_hash": text_hash(quote)}
    insert(conn, "candidate_memory", id="portable-memory",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           source_span_id="portable-span", entity_id=entity_id, kind="人物",
           payload_json=json_text({**evidence, "name": chapter_id, "note": chapter_id}),
           status="accepted", confidence=0.75, created_at=STAMP,
           reviewed_at=STAMP, reviewed_by=SOURCE_ACTOR, created_by=SOURCE_ACTOR)
    insert(conn, "assertion", id="portable-assertion", project_id=SOURCE_PROJECT,
           branch_id=SOURCE_BRANCH, subject_entity_id=entity_id, predicate="携带",
           object_json=json_text({"value": chapter_id}), epistemic_mode="world_truth",
           state="canon", confidence=0.75, reviewed_by=SOURCE_ACTOR, reviewed_at=STAMP,
           evidence_json=json_text([{"candidate_memory_id": "portable-memory",
                                     "source_span_id": "portable-span"}]))
    event = server.append_event(conn, chapter_id, "chapter.fixture",
                                {"chapter_id": chapter_id, "title": chapter_id,
                                 "body": chapter_id}, expected_head=revision)
    insert(conn, "character_state", id="portable-character-state",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH, entity_id=entity_id,
           attribute=chapter_id, value_json=json_text({"value": chapter_id}),
           event_id=event, source_assertion_id="portable-assertion",
           knowledge_scope_json=json_text({"known_by": [entity_id],
                                           "story_time": chapter_id}))
    insert(conn, "app_story_relation", id="portable-relation",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           from_entity_id=entity_id, to_entity_id=second_entity,
           relation_type="同行", note=chapter_id, status="active",
           created_at=STAMP, updated_at=STAMP, evidence_json=json_text(evidence))
    insert(conn, "app_timeline_event", id="portable-timeline",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH, chapter_id=chapter_id,
           title=chapter_id, event_time=chapter_id, entity_ids_json=json_text(entity_ids),
           summary=chapter_id, created_at=STAMP, updated_at=STAMP,
           evidence_json=json_text(evidence))
    insert(conn, "app_plot_item", id="portable-plot", project_id=SOURCE_PROJECT,
           branch_id=SOURCE_BRANCH, stage="setup", text=chapter_id,
           created_at=STAMP, updated_at=STAMP, archived=1)
    insert(conn, "app_foreshadow_link", id="portable-foreshadow",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           plot_id="portable-plot", chapter_id=chapter_id, kind="plant",
           note=chapter_id, created_at=STAMP, updated_at=STAMP,
           evidence_json=json_text(evidence))
    insert(conn, "plot_thread", id="portable-thread", project_id=SOURCE_PROJECT,
           branch_id=SOURCE_BRANCH, status="open",
           payload_json=json_text({"title": chapter_id, "source_chapter_id": chapter_id}),
           severity="suggestion")
    latest_body = first_body + "\r\n作者后来新增的一句。"
    server.update_chapter(conn, chapter_id, {"body": latest_body})
    latest_revision = server.chapter_rows(conn)[0]["revision_id"]
    insert(conn, "merge_conflict", id="portable-conflict",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           merge_base=revision, left_revision=revision,
           right_revision=latest_revision, conflict_type="text",
           payload_json=json_text({"reason": chapter_id}),
           status="resolved", resolver=SOURCE_ACTOR, resolved_at=STAMP)
    deleted_chapter = server.create_chapter(conn, {
        "id": "portable-deleted-chapter", "title": chapter_id,
        "body": "保留被删除章节的历史。", "status": "草稿",
    })
    deleted_revision = conn.execute(
        "SELECT id FROM document_revision WHERE document_node_id=? ORDER BY rowid DESC",
        (deleted_chapter,),
    ).fetchone()[0]
    insert(conn, "app_deleted_node", chapter_id=deleted_chapter,
           deleted_at=STAMP, reason=chapter_id)
    insert(conn, "deletion_event", id="portable-deletion",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           aggregate_id=deleted_chapter, revision_id=deleted_revision,
           reason=chapter_id, scope="chapter", created_at=STAMP, actor_id=SOURCE_ACTOR)
    insert(conn, "audit_event", id="portable-audit", project_id=SOURCE_PROJECT,
           actor_id=SOURCE_ACTOR, action="archive.fixture", target_id=chapter_id,
           before_hash=text_hash(first_body), after_hash=text_hash(latest_body),
           reason=chapter_id, created_at=STAMP)
    insert(conn, "branch", id=ALTERNATE_BRANCH, project_id=SOURCE_PROJECT,
           parent_id=SOURCE_BRANCH, merge_base_revision=revision,
           status="active", acl_json="{}")
    with workspace.use_scope(SOURCE_PROJECT, ALTERNATE_BRANCH, SOURCE_ACTOR):
        alternate_chapter = server.create_chapter(conn, {
            "id": "portable-alternate-chapter", "title": "另一稿件分支",
            "body": "另一分支的正文🙂。",
        })
    source = {"project_id": SOURCE_PROJECT, "branch_id": SOURCE_BRANCH,
              "chapter_id": chapter_id, "source_revision_id": latest_revision,
              "source_body_hash": text_hash(latest_body), "mode": "append",
              "range": {"start": len(latest_body), "end": len(latest_body), "quote": ""}}
    payload = {"model": chapter_id, "messages": [
        {"role": "system", "content": chapter_id},
        {"role": "user", "content": chapter_id}],
        "stop": [chapter_id]}
    audit = {"payload": payload, "request_sha256": text_hash(json_text(payload)),
             "messages_sha256": text_hash(json_text(payload["messages"])),
             "effective_parameters": {"stop": [chapter_id]}}
    for status in ("queued", "running", "success", "failed", "canceled", "unknown"):
        run_id, job_id = "portable-run-" + status, "portable-job-" + status
        output = chapter_id if status == "success" else ""
        context = {"_source": source, "current_chapter": {
            "id": chapter_id, "body": chapter_id, "revision_id": latest_revision},
            "author_notes": chapter_id, "request_audit": audit}
        result = {"text": output, "response_format": "text",
                  "request_audit": audit, "source": source, "mode": "provider",
                  "safe_to_apply": status == "success", "finish_reason": "stop",
                  "completion_state": "complete", "truncated": False,
                  "usage": {"prompt_tokens": 11, "completion_tokens": 3,
                            "total_tokens": 14}, "billing_state": "known",
                  "provider_event_id": chapter_id}
        insert(conn, "ai_run", id=run_id, project_id=SOURCE_PROJECT,
               branch_id=SOURCE_BRANCH, mode="provider", provider="synthetic",
               model=chapter_id, model_snapshot=chapter_id,
               system_prompt_hash=text_hash(chapter_id),
               prompt_hash=audit["request_sha256"],
               input_hash=text_hash(json_text(context)),
               output_hash=text_hash(output) if output else None,
               sampling_json=json_text({"stop": [chapter_id]}), tool_calls_json="[]",
               started_at=STAMP, ended_at=STAMP if status not in {"queued", "running"} else None,
               status=status, error=None, context_item_ids_json=json_text([entity_id]),
               token_json=json_text({"usage": result["usage"], "usage_state": "known"}),
               latency_ms=9, cost_json=json_text({"actual_cost": 0.002,
                                                 "billing_state": "known"}),
               author_decision="pending", consent_json=json_text({
                   "source": source, "job_id": job_id, "request_audit": audit,
                   "task": "continue", "response_format": "text"}))
        insert(conn, "app_ai_job", id=job_id, project_id=SOURCE_PROJECT,
               branch_id=SOURCE_BRANCH, ai_run_id=run_id, task="continue",
               chapter_id=chapter_id, instruction=chapter_id,
               context_json=json_text(context), response_format="text", max_tokens=100,
               status="failed" if status == "unknown" else status,
               retry_of="portable-run-failed" if status == "canceled" else None,
               result_json=json_text(result) if output else None,
               error="状态未知" if status == "unknown" else None,
               created_at=STAMP, updated_at=STAMP)
        insert(conn, "app_quota_reservation", ai_run_id=run_id,
               project_id=SOURCE_PROJECT, day="2026-10-15",
               status="dispatched" if status != "queued" else "reserved",
               created_at=STAMP, updated_at=STAMP)
    run_id = "portable-run-success"
    insert(conn, "app_ai_chunk", id="portable-chunk", project_id=SOURCE_PROJECT,
           branch_id=SOURCE_BRANCH, ai_run_id=run_id, seq=1, text=chapter_id,
           provider_event_id=chapter_id, event_name="message",
           payload_hash=text_hash(chapter_id), cumulative_hash=text_hash(chapter_id),
           created_at=STAMP)
    insert(conn, "app_ai_run_decision", id="portable-decision", ai_run_id=run_id,
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH, decision="accepted",
           output_hash=text_hash(chapter_id), reason=chapter_id,
           created_at=STAMP, actor_id=SOURCE_ACTOR)
    insert(conn, "app_candidate_application", ai_run_id=run_id,
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH, chapter_id=chapter_id,
           request_hash=text_hash("historic-application"),
           source_revision_id=revision, result_revision_id=latest_revision,
           decision_id="portable-decision", created_at=STAMP)
    binding = {"operation": "revise_candidate", "parent_run_id": run_id,
               "root_run_id": run_id, "parent_hash": text_hash(chapter_id),
               "parent_characters": len(chapter_id), "request_id": "portable-request",
               "request_hash": text_hash("portable-request")}
    child_context = {"_source": source, "_candidate_revision": binding,
                     "request_audit": audit}
    insert(conn, "ai_run", id="portable-child-run", project_id=SOURCE_PROJECT,
           branch_id=SOURCE_BRANCH, mode="provider", provider="synthetic",
           model=chapter_id, system_prompt_hash=text_hash(chapter_id),
           prompt_hash=audit["request_sha256"], input_hash=text_hash(json_text(child_context)),
           output_hash=text_hash(chapter_id), started_at=STAMP, ended_at=STAMP,
           status="success", author_decision="pending",
           consent_json=json_text({"source": source, "job_id": "portable-child-job",
                                   "candidate_revision": binding, "request_audit": audit}))
    insert(conn, "app_ai_job", id="portable-child-job", project_id=SOURCE_PROJECT,
           branch_id=SOURCE_BRANCH, ai_run_id="portable-child-run", task="continue",
           chapter_id=chapter_id, instruction=chapter_id,
           context_json=json_text(child_context), response_format="text",
           max_tokens=100, status="success", result_json=json_text({
               "text": chapter_id, "request_audit": audit, "source": source,
               "response_format": "text", "completion_state": "complete",
               "candidate_revision": binding}),
           created_at=STAMP, updated_at=STAMP)
    insert(conn, "app_candidate_revision", parent_run_id=run_id,
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           request_id="portable-request", request_hash=binding["request_hash"],
           candidate_hash=binding["parent_hash"], job_id="portable-child-job",
           created_at=STAMP)
    insert(conn, "app_assistant_request", token_hash=text_hash("old-confirmation"),
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           job_id="portable-job-success", created_at=STAMP)
    insert(conn, "run_manifest", id="portable-run-manifest", ai_run_id=run_id,
           project_id=SOURCE_PROJECT, sanitized_prompt=chapter_id,
           context_refs_json=json_text([entity_id]),
           sampling_json=json_text({"stop": [chapter_id]}),
           tool_results_json=json_text([{"text": chapter_id}]),
           author_actions_json=json_text([{"reason": chapter_id}]),
           consent_json=json_text({"reason": chapter_id}),
           redaction_policy="synthetic", manifest_hash=text_hash("historic-manifest"))
    insert(conn, "external_source", id="portable-external", project_id=SOURCE_PROJECT,
           uri="offline://synthetic", snapshot_hash=text_hash(quote),
           license_json=json_text({"note": chapter_id}), trust_level="author_review")
    insert(conn, "prov_entity", id="portable-provenance-entity",
           project_id=SOURCE_PROJECT, entity_type="revision", entity_ref=revision,
           content_hash=text_hash(first_body), created_at=STAMP)
    insert(conn, "prov_activity", id="portable-provenance-activity",
           project_id=SOURCE_PROJECT, activity_type="analyze", ai_run_id=run_id,
           event_id=event, started_at=STAMP)
    insert(conn, "prov_agent", id="portable-provenance-agent",
           project_id=SOURCE_PROJECT, agent_type="author", provider=chapter_id,
           version=chapter_id)
    insert(conn, "prov_edge", id="portable-provenance-edge",
           project_id=SOURCE_PROJECT, subject_id="portable-provenance-entity",
           predicate="wasGeneratedBy", object_id="portable-provenance-activity",
           qualified_json=json_text({"note": chapter_id}))
    insert(conn, "export_job", id="portable-export", project_id=SOURCE_PROJECT,
           branch_id=SOURCE_BRANCH, format="md", status="awaiting_confirmation",
           manifest_hash=text_hash("export"),
           loss_manifest_json=json_text({"note": chapter_id}),
           requested_by=SOURCE_ACTOR, confirmed_by=SOURCE_ACTOR,
           created_at=STAMP, completed_at=STAMP)
    conn.execute("UPDATE export_job SET status='success' WHERE id='portable-export'")
    insert(conn, "app_workspace", project_id=SOURCE_PROJECT, archived=0,
           created_at=STAMP, updated_at=STAMP)
    blueprint = {"premise": chapter_id, "genre": "悬疑", "audience": "成人",
                 "tone": chapter_id, "contract": chapter_id, "outline": "",
                 "volumes": [{"id": "portable-volume-plan", "title": chapter_id,
                              "summary": chapter_id, "chapters": [{
                                  "id": "portable-chapter-plan", "title": chapter_id,
                                  "goal": chapter_id, "pov": chapter_id, "hook": chapter_id,
                                  "chapter_id": chapter_id, "scenes": [{
                                      "id": "portable-scene-plan", "title": chapter_id,
                                      "goal": chapter_id, "location": chapter_id,
                                      "pov": chapter_id}]}]}]}
    insert(conn, "app_blueprint", project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           version=2, payload_json=json_text(blueprint), updated_at=STAMP)
    insert(conn, "app_chapter_structure", chapter_id=chapter_id,
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           volume_id="portable-volume-plan", plan_id="portable-chapter-plan",
           scenes_json=json_text(blueprint["volumes"][0]["chapters"][0]["scenes"]),
           updated_at=STAMP)
    insert(conn, "app_chapter_commit", id="portable-commit",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH, chapter_id=chapter_id,
           stage="postcommit", status="passed", revision_id=latest_revision,
           review_json=json_text({"message": chapter_id}),
           snapshot_json=json_text({"title": chapter_id, "body": chapter_id}),
           override_reason=chapter_id, created_at=STAMP, updated_at=STAMP)
    insert(conn, "app_model_settings", project_id=SOURCE_PROJECT,
           settings_json=json_text({
               "base_url": "https://synthetic.invalid/v1", "model": chapter_id,
               "models": {"continue": chapter_id},
               "styles": {"tone": chapter_id, "instructions": chapter_id},
               "parameters": {"temperature": 0.5, "max_tokens": 100,
                              "stop": [chapter_id]},
               "daily_limit": 20, "_credential_ref": None, "last_probe": None}),
           updated_at=STAMP)
    scope = {"project_id": SOURCE_PROJECT, "branch_id": SOURCE_BRANCH,
             "actor_id": SOURCE_ACTOR}
    manuscript_raw = ("\ufeff第一章 原导入\r\n这份原稿后来编辑过。🙂𠀀\r\n").encode("utf-8")
    parsed = manuscript_import.parse_import(manuscript_raw, "synthetic-original.md")
    import_record = manuscript_import.save_import_source(conn, scope, parsed, manuscript_raw)
    # The entire reference text is a legal literal equal to an old chapter ID.
    reference_raw = chapter_id.encode("utf-8")
    parsed_reference = manuscript_import.parse_import(reference_raw, "synthetic-reference.txt")
    reference = manuscript_import.save_reference(
        conn, scope, parsed_reference,
        {"mode": "reference", "title": chapter_id,
         "source_sha256": parsed_reference["source_sha256"]}, reference_raw,
    )
    reference_section = conn.execute(
        "SELECT id FROM app_reference_section WHERE document_id=?", (reference["id"],)
    ).fetchone()[0]
    gb_raw = "第一章 旧案\r\n𠀀与中文原编码必须保留。\r\n".encode("gb18030")
    gb_parsed = manuscript_import.parse_import(gb_raw, "synthetic-gb18030.txt")
    gb_reference = manuscript_import.save_reference(
        conn, scope, gb_parsed,
        {"mode": "reference", "title": "原编码参考",
         "source_sha256": gb_parsed["source_sha256"]}, gb_raw,
    )
    passage = {
        "id": reference_section + ":0:" + str(len(chapter_id)),
        "document_id": reference["id"], "section_id": reference_section,
        "project_id": SOURCE_PROJECT, "branch_id": SOURCE_BRANCH,
        "section_start": 0, "section_end": len(chapter_id),
        "source_sha256": digest(reference_raw),
        "section_hash": text_hash(chapter_id), "source_hash": text_hash(chapter_id),
        "source_quote": chapter_id, "source_start": 0, "source_end": len(chapter_id),
        "document_title": chapter_id, "title": parsed_reference["chapters"][0]["title"],
        "filename": parsed_reference["filename"], "ordinal": 0,
        "offset_unit": "unicode_codepoint", "material_kind": "reference",
        "use": "narrative_technique_only_not_story_fact",
    }
    reference_source = dict(conn.execute(
        "SELECT s.id,s.document_id,s.project_id,s.branch_id,s.ordinal,s.title,s.body,s.source_json,"
        "d.filename,d.title document_title,d.source_sha256,d.text_sha256,d.metadata_json "
        "FROM app_reference_section s JOIN app_reference_document d ON d.id=s.document_id "
        "WHERE s.id=?", (reference_section,),
    ).fetchone())
    for field in ("source_json", "metadata_json"):
        reference_source[field] = json.loads(reference_source[field])
    reference_source["evidence_sources"] = []
    marker = {
        "version": 1, "project_id": SOURCE_PROJECT, "branch_id": SOURCE_BRANCH,
        "dependencies": [{"kind": "reference_section", "id": reference_section,
                          "sha256": planning._hash(reference_source)}],
    }
    reference_payload = {"model": chapter_id, "messages": [
        {"role": "system", "content": chapter_id},
        {"role": "user", "content": json_text({"reference_passages": [passage],
                                              "current_chapter": {"body": chapter_id}})}]}
    reference_audit = {
        "payload": reference_payload,
        "request_sha256": text_hash(json_text(reference_payload)),
        "messages_sha256": text_hash(json_text(reference_payload["messages"])),
        "effective_parameters": {"stop": [chapter_id]},
    }
    # Use the saved passage contract while keeping its exact original request
    # separately frozen. Restore may update navigation only outside that audit.
    for job in conn.execute("SELECT * FROM app_ai_job ORDER BY rowid").fetchall():
        context = json.loads(job["context_json"])
        context.update(reference_passages=[passage], _material_source=marker,
                       request_audit=reference_audit)
        consent = json.loads(conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                         (job["ai_run_id"],)).fetchone()[0])
        consent["request_audit"] = reference_audit
        result = json.loads(job["result_json"]) if job["result_json"] else None
        if result:
            result["request_audit"] = reference_audit
        conn.execute("UPDATE app_ai_job SET context_json=?,result_json=? WHERE id=?",
                     (json_text(context), json_text(result) if result else None, job["id"]))
        conn.execute("UPDATE ai_run SET consent_json=?,input_hash=?,prompt_hash=? WHERE id=?",
                     (json_text(consent), text_hash(json_text(context)),
                      reference_audit["request_sha256"], job["ai_run_id"]))
    chapter_input = {"id": chapter_id, "body": latest_body,
                     "revision_id": latest_revision}
    analysis.save_analysis(conn, scope, chapter_input, None, {
        "summary": {"text": chapter_id, "evidence": [{"source_quote": quote}]},
        "handoff": {"text": chapter_id, "evidence": [{"source_quote": quote}]},
        "candidates": [],
    })
    analysis.save_review(conn, scope, chapter_input, None, {
        "findings": [{"category": "style", "severity": "suggestion",
                      "message": chapter_id, "evidence": [{"source_quote": quote}]}],
        "uncertainties": [],
    })
    frozen_hash = planning._hash({"version": 2, "blueprint": blueprint})
    candidate = {"premise": chapter_id, "genre": "悬疑",
                 "audience": "成人", "tone": chapter_id, "contract": chapter_id}
    for workflow_id, status in (("portable-workflow", "active"),
                                ("portable-completed-workflow", "completed")):
        insert(conn, "app_planning_workflow", id=workflow_id,
               project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH, status=status,
               current_stage="overview", stage_index=0, version=3,
               initial_json=json_text(candidate), blueprint_json=json_text(blueprint),
               source_blueprint_json=json_text(blueprint),
               final_blueprint_json=json_text(blueprint) if status == "completed" else None,
               source_version=2, source_hash=frozen_hash,
               created_at=STAMP, updated_at=STAMP)
    insert(conn, "app_planning_attempt", id="portable-plan-attempt",
           workflow_id="portable-workflow", stage="overview", attempt_no=1,
           status="complete", job_id=None, source_version=2, source_hash=frozen_hash,
           candidate_json=json_text(candidate), candidate_hash=planning._hash(candidate),
           max_tokens=100, created_at=STAMP, updated_at=STAMP)
    insert(conn, "app_planning_event", id="portable-plan-event",
           workflow_id="portable-workflow", attempt_id="portable-plan-attempt",
           event_type="stage.completed", payload_json=json_text({"reason": chapter_id}),
           created_at=STAMP)
    insert(conn, "app_planning_application", id="portable-plan-application",
           workflow_id="portable-completed-workflow", project_id=SOURCE_PROJECT,
           branch_id=SOURCE_BRANCH, source_version=2, source_hash=frozen_hash,
           final_hash=planning._hash(blueprint), result_version=3, applied_at=STAMP)
    insert(conn, "app_planning_workflow", id="portable-queued-workflow",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH, status="active",
           current_stage="overview", stage_index=0, version=1,
           initial_json=json_text(candidate), blueprint_json=json_text(blueprint),
           source_blueprint_json=json_text(blueprint), final_blueprint_json=None,
           source_version=2, source_hash=frozen_hash, created_at=STAMP, updated_at=STAMP)
    insert(conn, "app_planning_attempt", id="portable-queued-plan-attempt",
           workflow_id="portable-queued-workflow", stage="overview", attempt_no=1,
           status="queued", job_id="portable-job-queued", source_version=2,
           source_hash=frozen_hash, candidate_json=None, candidate_hash=None,
           max_tokens=100, created_at=STAMP, updated_at=STAMP)
    planning_context = {
        "project": {"id": SOURCE_PROJECT, "genre": "悬疑"}, "blueprint": blueprint,
        "planning_workflow": {"id": "portable-queued-workflow", "stage": "overview",
                              "source_version": 2,
                              "attempt_id": "portable-queued-plan-attempt"},
        "request_audit": audit,
    }
    planning_consent = {"task": "outline", "job_id": "portable-job-queued",
                        "request_audit": audit, "source": None}
    conn.execute(
        "UPDATE app_ai_job SET task='outline',chapter_id=NULL,context_json=?,"
        "response_format='json' WHERE id='portable-job-queued'",
        (json_text(planning_context),),
    )
    conn.execute(
        "UPDATE ai_run SET consent_json=?,input_hash=?,prompt_hash=? "
        "WHERE id='portable-run-queued'",
        (json_text(planning_consent), text_hash(json_text(planning_context)),
         audit["request_sha256"]),
    )
    insert(conn, "app_agent_session", id="portable-agent-session",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH, chapter_id=chapter_id,
           request_id="portable-agent-request", query=chapter_id,
           plan_json=json_text({"project_id": SOURCE_PROJECT, "chapter_id": chapter_id,
                                "historical_text": chapter_id}), status="running",
           created_at=STAMP, updated_at=STAMP, restored_read_only=0)
    for key, parent, raw in (
            ("portable-agent-input", None, b"\x80\x04opaque-input\x00\xff"),
            ("portable-agent-latest", "portable-agent-input", b"\x80\x04opaque-latest\x00\xfe")):
        insert(conn, "app_agent_checkpoint", id=key + "-row",
               project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
               session_id="portable-agent-session", checkpoint_key=key,
               checkpoint_ns="", parent_key=parent, type="sealed-test-bytes",
               checkpoint=raw,
               metadata=json_text({"chapter_id": chapter_id, "source": SOURCE_PROJECT}))
    insert(conn, "app_agent_write", id="portable-agent-write",
           project_id=SOURCE_PROJECT, branch_id=SOURCE_BRANCH,
           session_id="portable-agent-session", checkpoint_key="portable-agent-latest",
           checkpoint_ns="", task_id="portable-agent-task", task_path="prepare",
           idx=0, channel="sealed-history", type="sealed-test-bytes",
           value=b"\x80\x04opaque-write\x00\xfd")
    conn.commit()
    return {"chapter_id": chapter_id, "revision_id": revision,
            "latest_revision_id": latest_revision, "body": latest_body,
            "first_body": first_body, "entity_id": entity_id,
            "second_entity_id": second_entity, "event_id": event,
            "alternate_chapter_id": alternate_chapter,
            "reference_id": reference["id"], "reference_section_id": reference_section,
            "reference_raw": reference_raw, "gb_reference_id": gb_reference["id"],
            "gb_raw": gb_raw, "import_id": import_record["id"],
            "manuscript_raw": manuscript_raw, "reference_passage": passage}


# These are identities whose columns intentionally do not declare SQLite FKs.
EXTRA_IDENTITY_COLUMNS = {
    "branch": {"head_revision", "merge_base_revision"},
    "document_revision": {"actor_id"},
    "event_log": {"aggregate_id", "actor_id", "expected_head"},
    "deletion_event": {"aggregate_id", "actor_id"},
    "candidate_memory": {"reviewed_by", "created_by"},
    "merge_conflict": {"merge_base", "left_revision", "right_revision", "resolver"},
    "audit_event": {"actor_id", "target_id"},
    "assertion": {"reviewed_by"},
    "character_state": {"event_id"},
    "prov_entity": {"entity_ref"},
    "prov_edge": {"subject_id", "object_id"},
    "export_job": {"requested_by", "confirmed_by"},
    "app_ai_job": {"retry_of"},
    "app_candidate_application": {"source_revision_id", "result_revision_id", "decision_id"},
    "app_chapter_analysis": {"project_id", "branch_id", "chapter_id",
                             "source_revision_id", "run_id"},
    "app_story_review": {"project_id", "branch_id", "chapter_id",
                         "source_revision_id", "run_id"},
    "app_planning_workflow": {"project_id", "branch_id"},
    "app_planning_attempt": {"job_id"},
    "app_planning_event": {"attempt_id"},
    "app_planning_application": {"project_id", "branch_id"},
}
EVIDENCE_IDENTITY_KEYS = {
    "source_span_id", "source_chapter_id", "source_revision_id",
    "candidate_memory_id", "subject_entity_id", "entity_id",
    "from_entity_id", "to_entity_id", "event_id", "assertion_id",
    "character_state_id", "plot_id", "timeline_id", "decision_id",
}


def map_evidence(value, mapping):
    """Map this fixture's evidence structure without touching prose values."""
    if isinstance(value, list):
        return [map_evidence(item, mapping) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    return {key: (mapping.get(item, item) if key in EVIDENCE_IDENTITY_KEYS
                  and isinstance(item, str) else map_evidence(item, mapping))
            for key, item in value.items()}


def map_source(source, mapping):
    result = copy.deepcopy(source)
    for field in ("project_id", "branch_id", "chapter_id", "source_revision_id"):
        if field in result:
            result[field] = mapping[result[field]]
    return result


def map_binding(binding, mapping):
    result = copy.deepcopy(binding)
    for field in ("parent_run_id", "root_run_id"):
        if field in result:
            result[field] = mapping[result[field]]
    return result


def expected_json(table, column, raw, mapping):
    if table in backups.PLANNING_TABLES or table in AGENT_TABLES:
        return raw
    value = json.loads(raw)
    if column == "evidence_json" or (table == "candidate_memory" and column == "payload_json"):
        value = map_evidence(value, mapping)
    elif table == "event_log" and column == "payload_json":
        for field in ("chapter_id", "revision_id", "job_id", "decision_id"):
            if field in value:
                value[field] = mapping[value[field]]
    elif table == "plot_thread" and column == "payload_json":
        value = map_evidence(value, mapping)
    elif table == "app_timeline_event" and column == "entity_ids_json":
        value = [mapping[identifier] for identifier in value]
    elif table == "app_entity_scope" and column == "scope_json":
        value["known_by"] = [mapping.get(item, item) for item in value["known_by"]]
        inner = value["knowledge_scope"]
        inner["branches"] = [mapping[item] for item in inner["branches"]]
        for field in ("project_id", "subject_entity_id"):
            inner[field] = mapping[inner[field]]
    elif table == "character_state" and column == "knowledge_scope_json":
        value["known_by"] = [mapping[item] for item in value["known_by"]]
    elif table == "app_blueprint" and column == "payload_json":
        for volume in value["volumes"]:
            for chapter in volume["chapters"]:
                if chapter.get("chapter_id"):
                    chapter["chapter_id"] = mapping[chapter["chapter_id"]]
    elif table == "app_ai_job" and column in {"context_json", "result_json"}:
        for field in ("_source", "source"):
            if value.get(field):
                value[field] = map_source(value[field], mapping)
        current = value.get("current_chapter")
        if current:
            current["id"] = mapping[current["id"]]
            current["revision_id"] = mapping[current["revision_id"]]
        for field in ("_candidate_revision", "candidate_revision"):
            if value.get(field):
                value[field] = map_binding(value[field], mapping)
        if value.get("project"):
            value["project"]["id"] = mapping[value["project"]["id"]]
        if value.get("planning_workflow"):
            value["planning_workflow"]["id"] = mapping[value["planning_workflow"]["id"]]
            value["planning_workflow"]["attempt_id"] = mapping[
                value["planning_workflow"]["attempt_id"]]
        if value.get("blueprint"):
            for volume in value["blueprint"]["volumes"]:
                for chapter in volume["chapters"]:
                    if chapter.get("chapter_id"):
                        chapter["chapter_id"] = mapping[chapter["chapter_id"]]
        if value.get("_material_source"):
            marker = value["_material_source"]
            marker["project_id"] = mapping[marker["project_id"]]
            marker["branch_id"] = mapping[marker["branch_id"]]
            for dependency in marker["dependencies"]:
                dependency["id"] = mapping[dependency["id"]]
        for passage in value.get("reference_passages", []):
            for field in ("document_id", "section_id", "project_id", "branch_id"):
                passage[field] = mapping[passage[field]]
            passage["id"] = (passage["section_id"] + ":" + str(passage["section_start"])
                             + ":" + str(passage["section_end"]))
        if column == "context_json":
            value["_restored_read_only"] = True
    elif table == "ai_run" and column == "consent_json":
        if value.get("source"):
            value["source"] = map_source(value["source"], mapping)
        if value.get("job_id"):
            value["job_id"] = mapping[value["job_id"]]
        if value.get("candidate_revision"):
            value["candidate_revision"] = map_binding(value["candidate_revision"], mapping)
        value["restored_read_only"] = True
    elif table == "ai_run" and column == "context_item_ids_json":
        value = [mapping.get(item, item) for item in value]
    elif table in {"app_chapter_analysis", "app_story_review"} and column == "payload_json":
        # Report identities are embedded by their owner; evidence/prose remain
        # independent of the top-level report navigation fields.
        for field in ("id", "project_id", "branch_id", "chapter_id",
                      "source_revision_id", "revision_id", "run_id"):
            if value.get(field) in mapping:
                value[field] = mapping[value[field]]
        for dependency in value.get("source_dependencies", []):
            for field in ("chapter_id", "revision_id"):
                if dependency.get(field) in mapping:
                    dependency[field] = mapping[dependency[field]]
        value = map_evidence(value, mapping)
    return value


class PortableBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.temporary = cls.stack.enter_context(tempfile.TemporaryDirectory(
            prefix="moyu-portable-fixtures-"
        ))
        cls.temp_root = Path(cls.temporary)
        cls.stack.enter_context(patch.dict(os.environ, {
            "MOYU_AI_API_KEY": "", "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "",
            "MOYU_SECRET_KEY": "",
            "MOYU_CREDENTIALS_DIR": str(cls.temp_root / "synthetic-credentials"),
        }))
        cls.provider = cls.stack.enter_context(patch.object(
            server, "call_ai_provider", side_effect=AssertionError("provider forbidden")
        ))
        cls.worker = cls.stack.enter_context(patch.object(
            server, "start_ai_worker", side_effect=AssertionError("worker forbidden")
        ))
        cls.stack.enter_context(patch.object(server, "PROJECT_ID", SOURCE_PROJECT))
        cls.stack.enter_context(patch.object(server, "BRANCH_ID", SOURCE_BRANCH))
        cls.stack.enter_context(patch.object(server, "ACTOR_ID", SOURCE_ACTOR))
        cls.source_path = cls.temp_root / "source-data" / "book.sqlite3"
        with patch.object(server, "DB_PATH", cls.source_path):
            server.init_db()
            with server.connect() as conn:
                cls.identities = seed_source(conn)
                cls.payload = backups.capture(conn, SOURCE_PROJECT)
                cls.source_snapshot = snapshot(conn)
                cls.metadata = {
                    name: [dict(row) for row in conn.execute(f'PRAGMA table_info("{name}")')]
                    for name in cls.payload["tables"]
                }
                cls.foreign_keys = {
                    name: [dict(row) for row in conn.execute(f'PRAGMA foreign_key_list("{name}")')]
                    for name in cls.payload["tables"]
                }
                cls.revision_orders = {
                    row["id"]: [revision[0] for revision in conn.execute(
                        "SELECT id FROM document_revision WHERE document_node_id=? ORDER BY rowid",
                        (row["id"],))]
                    for row in cls.payload["tables"]["document_node"]
                }
                if conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise AssertionError("synthetic source fixture has a broken FK")
        cls.source_file_hash = digest(cls.source_path.read_bytes())
        if set(cls.payload["tables"]) != ALL_TABLES:
            raise AssertionError("complete fixture must capture every documented table")
        empty = [name for name, rows in cls.payload["tables"].items() if not rows]
        if empty:
            raise AssertionError("complete fixture has untested empty tables: " + repr(empty))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.source_file_hash_after = digest(cls.source_path.read_bytes())
            if cls.source_file_hash_after != cls.source_file_hash:
                raise AssertionError("source database bytes changed")
            with sqlite3.connect(f"file:{cls.source_path}?mode=ro", uri=True) as source:
                if snapshot(source) != cls.source_snapshot:
                    raise AssertionError("source database contents changed")
            cls.provider.assert_not_called()
            cls.worker.assert_not_called()
        finally:
            cls.stack.close()

    def setUp(self):
        self.local = ExitStack()
        self.target_path = self.temp_root / self.id().rsplit(".", 1)[-1] / "target.sqlite3"
        self.local.enter_context(patch.object(server, "DB_PATH", self.target_path))
        self.local.enter_context(patch.object(server, "PROJECT_ID", "portable-target-book"))
        self.local.enter_context(patch.object(server, "BRANCH_ID", "portable-target-main"))
        self.local.enter_context(patch.object(server, "ACTOR_ID", "portable-target-author"))
        server.init_db()
        self.conn = server.connect()
        self.local.callback(self.conn.close)
        self.payload_copy = copy.deepcopy(self.payload)

    def tearDown(self):
        try:
            self.provider.assert_not_called()
            self.worker.assert_not_called()
        finally:
            self.local.close()

    def assert_reader_rejects(self, raw: bytes):
        before = snapshot(self.conn)
        with self.assertRaises(ValueError):
            backups.read_bytes(raw)
        self.assertEqual(snapshot(self.conn), before)

    def assert_invalid_payload(self, payload):
        before = snapshot(self.conn)
        with self.assertRaises(ValueError):
            backups.validate_payload(self.conn, payload)
        self.assertEqual(snapshot(self.conn), before)
        with self.assertRaises(ValueError):
            backups.transplant(self.conn, payload, "portable-rejected-copy")
        self.assertEqual(snapshot(self.conn), before,
                         "helper must reject or roll back without caller rollback")

    def restore(self, payload=None, project="portable-restored-book"):
        value = self.payload_copy if payload is None else payload
        raw = archive_bytes(value)
        loaded = backups.read_bytes(raw)
        before = snapshot(self.conn)
        backups.validate_payload(self.conn, loaded["payload"], manifest=loaded["manifest"])
        self.assertEqual(snapshot(self.conn), before, "preview validator writes to target")
        self.conn.execute("BEGIN IMMEDIATE")
        result = backups.transplant(self.conn, loaded["payload"], project)
        self.conn.commit()
        self.assertFalse(self.conn.execute("PRAGMA foreign_key_check").fetchall())
        return result

    def expected_row(self, table, source_row, mapping, title):
        expected = {key: decoded(value) for key, value in source_row.items()}
        identities = {row["name"] for row in self.metadata[table] if row["pk"]}
        identities |= {row["from"] for row in self.foreign_keys[table]}
        identities |= EXTRA_IDENTITY_COLUMNS.get(table, set())
        for field in identities:
            value = expected.get(field)
            if isinstance(value, str) and value in mapping:
                expected[field] = mapping[value]
        if table == "event_log":
            expected["idempotency_key"] = mapping[source_row["idempotency_key"]]
        if table == "app_assistant_request":
            expected["token_hash"] = text_hash(title + ":" + source_row["token_hash"])
        for field, value in list(expected.items()):
            if field.endswith("_json") and isinstance(value, str):
                expected[field] = expected_json(table, field, value, mapping)
        if table == "project":
            expected["name"] = source_row["name"] + backups.RESTORE_TITLE_SUFFIX
        if table == "app_ai_job" and source_row["status"] in {"queued", "running"}:
            expected.update(status="failed", cancel_requested=1)
            expected["error"] = "从备份恢复；旧请求不会自动再次调用模型"
        if table == "ai_run" and source_row["status"] in {"queued", "running"}:
            expected.update(status="unknown",
                            error="备份中的外部请求状态未知，未重复执行")
        if table == "app_planning_workflow":
            expected.update(status="canceled", version=source_row["version"] + 1,
                            cancel_reason=backups.PLANNING_RESTORE_REASON)
        if table == "app_planning_attempt" and source_row["status"] in {"queued", "running", "unknown"}:
            expected.update(status="unknown",
                            error="备份中的规划模型请求费用与执行状态未知，未重复执行。")
        if table == "app_agent_session":
            expected.update(status="restored", restored_read_only=1)
        return expected

    def assert_all_rows(self, payload, result):
        mapping = result["identity_map"]
        restored = backups.capture(self.conn, result["project_id"])
        self.assertEqual(set(restored["tables"]), ALL_TABLES)
        for table, source_rows in payload["tables"].items():
            with self.subTest(table=table):
                actual_rows = restored["tables"][table]
                self.assertEqual(len(actual_rows), len(source_rows))
                primary_key = [row["name"] for row in sorted(
                    self.metadata[table], key=lambda row: row["pk"]) if row["pk"]]
                actual_by_key = {
                    tuple(row[field] for field in primary_key): row for row in actual_rows
                }
                for source_row in source_rows:
                    expected = self.expected_row(table, source_row, mapping,
                                                 result["project_id"])
                    key = tuple(expected[field] for field in primary_key)
                    self.assertIn(key, actual_by_key)
                    actual_row = actual_by_key[key]
                    actual = {field: decoded(value) for field, value in actual_row.items()}
                    for field, value in list(actual.items()):
                        if field.endswith("_json") and isinstance(value, str):
                            if table in backups.PLANNING_TABLES or table in AGENT_TABLES:
                                continue
                            actual[field] = json.loads(value)
                    self.assertEqual(actual, expected, (table, source_row.get("id")))
        return restored

    def test_complete_archive_reader_returns_distinct_byte_fingerprints(self):
        raw = archive_bytes(self.payload_copy)
        loaded = backups.read_bytes(raw, SOURCE_PROJECT)
        self.assertEqual(loaded["payload"], self.payload_copy)
        self.assertEqual(loaded["archive_sha256"], digest(raw))
        self.assertEqual(loaded["size_bytes"], len(raw))
        self.assertEqual(loaded["sha256"], loaded["manifest"]["sha256"])
        self.assertNotEqual(loaded["archive_sha256"], loaded["sha256"])
        self.assertEqual(backups.read(io.BytesIO(raw), SOURCE_PROJECT), loaded)
        with tempfile.TemporaryDirectory(prefix="moyu-portable-path-") as directory:
            path = Path(directory) / "synthetic-portable.zip"
            path.write_bytes(raw)
            self.assertEqual(backups.read(path, SOURCE_PROJECT), loaded)

    def test_complete_round_trip_preserves_all_tables_and_target_records(self):
        original_target = snapshot(self.conn)
        result = self.restore()
        mapping = result["identity_map"]
        self.assertEqual(mapping[SOURCE_PROJECT], result["project_id"])
        for table, rows in self.payload["tables"].items():
            for row in rows:
                if row.get("id"):
                    self.assertNotEqual(mapping[row["id"]], row["id"])
        for table, rows in original_target["tables"].items():
            actual = snapshot(self.conn)["tables"][table]
            self.assertEqual(actual[:len(rows)], rows, table)
        restored = self.assert_all_rows(self.payload, result)
        for old_node, revisions in self.revision_orders.items():
            with self.subTest(revision_order=old_node):
                actual = [row[0] for row in self.conn.execute(
                    "SELECT id FROM document_revision WHERE document_node_id=? ORDER BY rowid",
                    (mapping[old_node],))]
                self.assertEqual(actual, [mapping[identity] for identity in revisions])
        # Exporting a restored book and importing it again checks that the new
        # identity graph is self-contained rather than tied to the first map.
        second = self.restore(restored, "portable-second-copy")
        self.assertEqual(second["row_counts"], result["row_counts"])
        for old in mapping.values():
            if old in second["identity_map"]:
                self.assertNotEqual(second["identity_map"][old], old)
        for identity in ("reference_id", "gb_reference_id"):
            first_id = mapping[self.identities[identity]]
            second_id = second["identity_map"][first_id]
            self.assertEqual(self.conn.execute(
                "SELECT source_bytes FROM app_reference_document WHERE id=?", (second_id,)
            ).fetchone()[0], self.identities["reference_raw" if identity == "reference_id" else "gb_raw"])

    def test_validation_of_valid_complete_payload_is_read_only_and_stable(self):
        before = snapshot(self.conn)
        payload_before = copy.deepcopy(self.payload_copy)
        for _ in range(3):
            backups.validate_payload(self.conn, self.payload_copy)
        self.assertEqual(snapshot(self.conn), before)
        self.assertEqual(self.payload_copy, payload_before)
        self.assertFalse(self.conn.in_transaction)

    def test_duplicate_zip_members_are_rejected(self):
        for member in ("project.json", "manifest.json"):
            with self.subTest(member=member):
                self.assert_reader_rejects(archive_bytes(self.payload_copy, duplicate=member))

    def test_zip_container_damage_member_types_and_extra_members_are_rejected(self):
        raw = archive_bytes(self.payload_copy, compression=zipfile.ZIP_STORED)
        cases = {
            "not_zip": b"portable synthetic text",
            "truncated": raw[:-12],
            "crc_mismatch": corrupted_crc(raw),
            "extra_file": archive_bytes(self.payload_copy, extras=[("extra.json", b"{}")]),
            "directory": archive_bytes(self.payload_copy, extras=[("unused/", b"")]),
            "directory_member_mode": archive_bytes(
                self.payload_copy, member_mode=stat.S_IFDIR | 0o755),
            "fifo_member_mode": archive_bytes(
                self.payload_copy, member_mode=stat.S_IFIFO | 0o600),
            "symlink": archive_bytes(self.payload_copy, member_mode=stat.S_IFLNK | 0o777),
            "encrypted": patch_zip_header(raw, "flags", 1),
            "unsupported_compression": patch_zip_header(raw, "compression", 99),
            "expanded_limit": patch_zip_header(raw, "size", 256_000_001),
        }
        for name, archive in cases.items():
            with self.subTest(case=name):
                self.assert_reader_rejects(archive)
        with self.assertRaises(ValueError):
            backups.read_bytes("wrong input type")
        with self.assertRaises(ValueError):
            backups.read_bytes(b"")
        self.assert_reader_rejects(b"x" * (32 * 1024 * 1024 + 1))

    def test_json_top_level_duplicate_keys_nonfinite_and_encoding_are_rejected(self):
        valid_raw = json_text(self.payload_copy).encode("utf-8")
        cases = {
            "project_array": b"[]",
            "project_null": b"null",
            "project_duplicate_key": valid_raw.replace(
                b'{"format":', b'{"version":1,"format":', 1),
            "project_nan": valid_raw.replace(b'"version":1', b'"version":NaN', 1),
            "project_infinity": valid_raw.replace(b'"version":1', b'"version":Infinity', 1),
            "project_utf8": b'{"bad":"\xff"}',
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                self.assert_reader_rejects(archive_bytes(self.payload_copy, raw_project=raw))
        for manifest in (b"[]", b"null", b'{"sha256":NaN}',
                         b'{"project_id":"x","project_id":"y"}',
                         b'{"bad":"\xff"}'):
            with self.subTest(manifest=manifest):
                self.assert_reader_rejects(archive_bytes(
                    self.payload_copy, raw_manifest=manifest))

    def test_boolean_and_future_format_schema_versions_are_rejected(self):
        for key, value in (("version", True), ("version", False), ("version", 1.0),
                           ("version", "1"), ("version", 2),
                           ("schema_version", True), ("schema_version", 11.0),
                           ("schema_version", 999), ("schema_version", 0)):
            with self.subTest(key=key, value=value):
                payload = copy.deepcopy(self.payload)
                payload[key] = value
                if key == "schema_version":
                    payload["tables"]["project"][0]["schema_version"] = value
                self.assert_reader_rejects(archive_bytes(payload))
                self.assert_invalid_payload(payload)
        payload = copy.deepcopy(self.payload)
        payload["tables"]["project"][0]["schema_version"] = 999
        self.assert_reader_rejects(archive_bytes(payload))
        self.assert_invalid_payload(payload)

    def test_manifest_identity_scope_title_branch_counts_and_digest_are_bound(self):
        changes = (
            {"project_id": "unrelated-book"}, {"branch_id": "unrelated-branch"},
            {"project_title": "错误作品名"}, {"scope": "manuscript_only"},
            {"sha256": "0" * 64}, {"row_counts": {}}, {"row_counts": []},
            {"row_counts": {name: (True if name == "project" else len(rows))
                            for name, rows in self.payload["tables"].items()}},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assert_reader_rejects(archive_bytes(
                    self.payload_copy, manifest_overrides=change))
        with self.assertRaises(ValueError):
            backups.read_bytes(archive_bytes(self.payload_copy), "wrong-book")
        payload = copy.deepcopy(self.payload)
        payload["project_id"] = "wrong-payload-root"
        self.assert_reader_rejects(archive_bytes(payload))
        self.assert_invalid_payload(payload)

    def test_each_of_the_52_base_tables_is_required_even_when_counts_are_recomputed(self):
        for name in sorted(BASE_TABLES):
            with self.subTest(table=name):
                payload = copy.deepcopy(self.payload)
                payload["tables"].pop(name)
                self.assert_reader_rejects(archive_bytes(payload))
                self.assert_invalid_payload(payload)

    def test_only_known_additive_tables_can_be_absent(self):
        scope_legacy = copy.deepcopy(self.payload)
        scope_legacy["tables"].pop("app_entity_scope")
        scope_result = self.restore(scope_legacy, "portable-legacy-scope")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM app_entity_scope WHERE entity_id IN "
            "(SELECT id FROM entity WHERE project_id=?)", (scope_result["project_id"],)
        ).fetchone()[0], 0)
        legacy = copy.deepcopy(self.payload)
        legacy["tables"].pop("app_entity_scope")
        legacy["tables"].pop("app_candidate_revision")
        for table in AGENT_TABLES:
            legacy["tables"].pop(table)
        # A genuine pre-candidate package has no parent/child candidate bind.
        for row in legacy["tables"]["app_ai_job"]:
            context = json.loads(row["context_json"])
            context.pop("_candidate_revision", None)
            row["context_json"] = json_text(context)
            if row["result_json"]:
                result = json.loads(row["result_json"])
                result.pop("candidate_revision", None)
                row["result_json"] = json_text(result)
        for row in legacy["tables"]["ai_run"]:
            consent = json.loads(row["consent_json"])
            consent.pop("candidate_revision", None)
            row["consent_json"] = json_text(consent)
        result = self.restore(legacy, "portable-base-52-copy")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM app_candidate_revision WHERE project_id=?",
            (result["project_id"],),
        ).fetchone()[0], 0)
        for table in AGENT_TABLES:
            self.assertEqual(self.conn.execute(
                'SELECT COUNT(*) FROM "' + table + '" WHERE project_id=?',
                (result["project_id"],),
            ).fetchone()[0], 0)
        malformed = copy.deepcopy(self.payload)
        malformed["tables"].pop("app_candidate_revision")
        self.assert_invalid_payload(malformed)

    def test_unknown_table_unknown_column_missing_column_and_bad_rows_rejected(self):
        cases = []
        payload = copy.deepcopy(self.payload)
        payload["tables"]["unknown_future_table"] = []
        cases.append(("unknown_table", payload))
        for field, value in (("future_column", "future"), ("created_at", None),
                             ("schema_version", "11")):
            payload = copy.deepcopy(self.payload)
            payload["tables"]["project"][0][field] = value
            cases.append((field, payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["app_project_meta"][0].pop("book_status")
        cases.append(("missing_book_status", payload))
        for bad_rows in ({}, None, "wrong", [None], [7], [[]]):
            payload = copy.deepcopy(self.payload)
            payload["tables"]["entity"] = bad_rows
            cases.append(("bad_rows_" + repr(bad_rows), payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["entity"].append(copy.deepcopy(payload["tables"]["entity"][0]))
        cases.append(("duplicate_pk", payload))
        for name, payload in cases:
            with self.subTest(case=name):
                self.assert_invalid_payload(payload)

    def test_references_must_close_inside_package_not_existing_target_book(self):
        foreign_entity = self.conn.execute("SELECT id FROM entity ORDER BY rowid").fetchone()[0]
        foreign_actor = self.conn.execute("SELECT id FROM actor ORDER BY rowid").fetchone()[0]
        cases = (
            ("app_story_relation", "from_entity_id", foreign_entity),
            ("document_revision", "actor_id", foreign_actor),
            ("source_span", "revision_id", "missing-revision"),
            ("app_ai_job", "retry_of", "missing-parent-run"),
            ("app_reference_section", "document_id", "missing-reference"),
            ("app_candidate_application", "source_revision_id", "missing-revision"),
            ("app_candidate_application", "decision_id", "missing-decision"),
            ("app_chapter_analysis", "run_id", "missing-run"),
            ("prov_entity", "entity_ref", "missing-revision"),
            ("prov_edge", "object_id", "missing-provenance"),
            ("app_planning_event", "attempt_id", "missing-attempt"),
            ("app_agent_session", "chapter_id", "missing-chapter"),
            ("app_agent_checkpoint", "session_id", "missing-agent-session"),
            ("app_agent_write", "session_id", "missing-agent-session"),
        )
        for table, field, value in cases:
            with self.subTest(table=table, field=field):
                payload = copy.deepcopy(self.payload)
                payload["tables"][table][0][field] = value
                self.assert_invalid_payload(payload)

    def test_project_and_branch_ownership_must_match_related_records(self):
        cases = (
            ("branch", "project_id", "foreign-project"),
            ("entity", "project_id", "foreign-project"),
            ("app_ai_job", "branch_id", ALTERNATE_BRANCH),
            ("app_reference_section", "branch_id", ALTERNATE_BRANCH),
            ("source_span", "document_node_id", self.identities["alternate_chapter_id"]),
            ("app_candidate_application", "branch_id", ALTERNATE_BRANCH),
            ("app_chapter_analysis", "branch_id", ALTERNATE_BRANCH),
            ("app_story_review", "branch_id", ALTERNATE_BRANCH),
            ("app_candidate_revision", "branch_id", ALTERNATE_BRANCH),
            ("app_planning_application", "branch_id", ALTERNATE_BRANCH),
            ("character_state", "branch_id", ALTERNATE_BRANCH),
            ("app_agent_session", "branch_id", ALTERNATE_BRANCH),
            ("app_agent_checkpoint", "branch_id", ALTERNATE_BRANCH),
            ("app_agent_write", "branch_id", ALTERNATE_BRANCH),
            ("app_agent_session", "project_id", "foreign-project"),
            ("app_agent_checkpoint", "project_id", "foreign-project"),
            ("app_agent_write", "project_id", "foreign-project"),
        )
        for table, field, value in cases:
            with self.subTest(table=table, field=field):
                payload = copy.deepcopy(self.payload)
                payload["tables"][table][0][field] = value
                self.assert_invalid_payload(payload)

    def test_agent_checkpoints_and_writes_require_the_same_session_and_namespace(self):
        for table, row_index, field, value in (
                ("app_agent_checkpoint", 1, "parent_key", "missing-checkpoint"),
                ("app_agent_checkpoint", 1, "checkpoint_ns", "foreign-namespace"),
                ("app_agent_write", 0, "checkpoint_key", "missing-checkpoint"),
                ("app_agent_write", 0, "checkpoint_ns", "foreign-namespace")):
            with self.subTest(table=table, field=field):
                payload = copy.deepcopy(self.payload)
                payload["tables"][table][row_index][field] = value
                self.assert_invalid_payload(payload)
        for table, row_index in (("app_agent_checkpoint", 1), ("app_agent_write", 0)):
            with self.subTest(table=table, field="session_id"):
                payload = copy.deepcopy(self.payload)
                other = copy.deepcopy(payload["tables"]["app_agent_session"][0])
                other.update(id="portable-agent-other", request_id="portable-agent-other-request")
                payload["tables"]["app_agent_session"].append(other)
                payload["tables"][table][row_index]["session_id"] = other["id"]
                self.assert_invalid_payload(payload)
        for missing_table in AGENT_TABLES:
            with self.subTest(missing_table=missing_table):
                payload = copy.deepcopy(self.payload)
                payload["tables"].pop(missing_table)
                if missing_table == "app_agent_write":
                    backups.validate_payload(self.conn, payload)
                else:
                    self.assert_invalid_payload(payload)

    def test_agent_checkpoint_binary_contract_rejects_malformed_archives(self):
        for table, field in (("app_agent_checkpoint", "checkpoint"),
                             ("app_agent_write", "value")):
            for value in (None, "opaque", {"$blob": "@@@"},
                          {"$blob": "YQ==", "unexpected": 1}, {"$blob": 10}):
                with self.subTest(table=table, value=value):
                    payload = copy.deepcopy(self.payload)
                    payload["tables"][table][0][field] = value
                    self.assert_invalid_payload(payload)
        for plan in ([], None, "history", {"summary": []}, {"steps": []},
                     {"steps": {"continue": "invalid"}}):
            with self.subTest(plan=plan):
                payload = copy.deepcopy(self.payload)
                payload["tables"]["app_agent_session"][0]["plan_json"] = json_text(plan)
                self.assert_invalid_payload(payload)

    def test_agent_history_rejects_invalid_modes_without_target_changes(self):
        for mode in ([], {}, ["inspect"], None, True, False, 0, 1, "", "unknown", "Inspect"):
            with self.subTest(mode=mode):
                payload = copy.deepcopy(self.payload)
                row = payload["tables"]["app_agent_session"][0]
                plan = json.loads(row["plan_json"])
                plan["mode"] = mode
                row["plan_json"] = json_text(plan)
                before = snapshot(self.conn)
                loaded = backups.read_bytes(archive_bytes(payload))
                self.assert_invalid_payload(loaded["payload"])
                self.assertEqual(snapshot(self.conn), before)

    def test_agent_history_rejects_nontext_summary_fields_without_target_changes(self):
        for field in ("stage", "status", "error", "current_job_id", "current_ai_run_id"):
            for value in (None, [], {}, True, 0, 1.5):
                with self.subTest(field=field, value=value):
                    payload = copy.deepcopy(self.payload)
                    row = payload["tables"]["app_agent_session"][0]
                    plan = json.loads(row["plan_json"])
                    plan["summary"] = {field: value}
                    row["plan_json"] = json_text(plan)
                    before = snapshot(self.conn)
                    loaded = backups.read_bytes(archive_bytes(payload))
                    self.assert_invalid_payload(loaded["payload"])
                    self.assertEqual(snapshot(self.conn), before)

    def test_agent_history_rejects_invalid_call_counts_without_target_changes(self):
        for field, invalid in (
                ("calls_used", (None, [], {}, True, False, "0", 0.0, 1.5, -1, 5, 2 ** 64)),
                ("max_calls", (None, [], {}, True, False, "1", 1.0, 1.5, -1, 0, 5, 2 ** 64))):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    payload = copy.deepcopy(self.payload)
                    row = payload["tables"]["app_agent_session"][0]
                    plan = json.loads(row["plan_json"])
                    plan["summary"] = {field: value}
                    row["plan_json"] = json_text(plan)
                    before = snapshot(self.conn)
                    loaded = backups.read_bytes(archive_bytes(payload))
                    self.assert_invalid_payload(loaded["payload"])
                    self.assertEqual(snapshot(self.conn), before)

    def test_valid_agent_history_modes_and_legacy_defaults_remain_readable(self):
        for case, mode in enumerate(("continue", "polish", "inspect", "plan", None)):
            with self.subTest(mode=mode):
                payload = copy.deepcopy(self.payload)
                row = payload["tables"]["app_agent_session"][0]
                plan = json.loads(row["plan_json"])
                if mode is not None:
                    plan.update(mode=mode, summary={
                        "stage": "", "status": "review_ready", "error": "",
                        "current_job_id": "", "current_ai_run_id": "",
                        "calls_used": 4 if mode == "plan" else 0,
                        "max_calls": 4 if mode == "plan" else 1,
                    }, steps={})
                row["plan_json"] = json_text(plan)
                result = self.restore(payload, "portable-valid-agent-history-" + str(case))
                restored = self.conn.execute(
                    "SELECT * FROM app_agent_session WHERE project_id=?",
                    (result["project_id"],),
                ).fetchone()
                self.assertEqual(restored["plan_json"], row["plan_json"])
                before = snapshot(self.conn)
                with patch.object(server, "project_id", return_value=result["project_id"]), \
                        patch.object(server, "branch_id", return_value=restored["branch_id"]):
                    sessions = [server.writing_agent.public_session(server, self.conn, restored)]
                self.assertEqual(sessions[0]["mode"], mode or "inspect")
                self.assertTrue(sessions[0]["restored_read_only"])
                self.assertEqual(sessions[0]["calls_used"], 4 if mode == "plan" else 0)
                self.assertEqual(snapshot(self.conn), before)

    def test_restored_agent_history_preserves_bytes_and_cannot_deserialize(self):
        from moyu_agent_checkpoint import ScopedCheckpointSaver

        first = self.restore()
        first_payload = self.assert_all_rows(self.payload_copy, first)
        second = self.restore(first_payload, "portable-agent-second-copy")
        for result in (first, second):
            session = self.conn.execute(
                "SELECT * FROM app_agent_session WHERE project_id=?",
                (result["project_id"],),
            ).fetchone()
            self.assertEqual((session["status"], session["restored_read_only"]), ("restored", 1))
            self.assertEqual(session["plan_json"],
                             self.payload["tables"]["app_agent_session"][0]["plan_json"])
            for table, field in (("app_agent_checkpoint", "checkpoint"),
                                 ("app_agent_write", "value")):
                restored = self.conn.execute(
                    'SELECT * FROM "' + table + '" WHERE project_id=? ORDER BY rowid',
                    (result["project_id"],),
                ).fetchall()
                source = self.payload["tables"][table]
                self.assertEqual([row[field] for row in restored],
                                 [decoded(row[field]) for row in source])
                self.assertTrue(all(row["session_id"] == session["id"] for row in restored))
                self.assertEqual([row["checkpoint_key"] for row in restored],
                                 [row["checkpoint_key"] for row in source])
            scope = {"project_id": result["project_id"], "branch_id": session["branch_id"]}
            saver = ScopedCheckpointSaver(self.conn, scope, session["id"])
            config = {"configurable": {"thread_id": saver.thread_id, "checkpoint_ns": ""}}
            before = snapshot(self.conn)
            with patch.object(saver.serde, "loads_typed", side_effect=AssertionError("sealed history decoded")) as load:
                for operation in (
                        lambda: saver.get_tuple(config),
                        lambda: list(saver.list(config)),
                        lambda: saver.put(config, {}, {}, {}),
                        lambda: saver.put_writes(config, [("x", {})], "blocked-task")):
                    with self.assertRaisesRegex(ValueError, "仅供回看"):
                        operation()
                load.assert_not_called()
            self.assertEqual(snapshot(self.conn), before)

    def test_real_langgraph_checkpoints_restore_as_sealed_history(self):
        from moyu_agent_checkpoint import ScopedCheckpointSaver
        import moyu_writing_agent as agent

        conn = server.connect(check_same_thread=False)
        self.local.callback(conn.close)
        chapter = server.chapter_rows(conn)[0]
        scope = {"project_id": "portable-target-book", "branch_id": "portable-target-main"}
        session_id = "portable-live-agent-history"
        insert(conn, "app_agent_session", id=session_id, **scope, chapter_id=chapter["id"],
               request_id="portable-live-agent-request", query="synthetic inspection",
               plan_json="{}", status="planned", created_at=STAMP, updated_at=STAMP,
               restored_read_only=0)
        calls = []

        def enqueue(state, task, step):
            calls.append(step)
            return {"id": "synthetic-job", "ai_run_id": "synthetic-run"}

        callbacks = agent.WritingAgentCallbacks(
            prepare=lambda state: {"status": "ready", "source_revision_id": "synthetic-revision",
                                   "source_hash": "synthetic-hash"},
            enqueue=enqueue,
            get_job=lambda state, job: {"id": job, "status": "queued"},
            verify_adoption=lambda state, answer: {"applied": False},
        )
        saver = ScopedCheckpointSaver(conn, scope, session_id)
        graph = agent.build_graph(callbacks, saver)
        state = agent.initial_state(session_id, scope["project_id"], scope["branch_id"],
                                    chapter["id"], "inspect", max_calls=2)
        self.assertIn("__interrupt__", agent.invoke_start(graph, state, thread_id=saver.thread_id))
        graph = agent.build_graph(callbacks, ScopedCheckpointSaver(conn, scope, session_id))
        result = agent.invoke_resume(graph, session_id, {"action": "authorize"}, thread_id=saver.thread_id)
        self.assertEqual(result["status"], "awaiting_job")
        self.assertEqual(len(calls), 1)
        conn.commit()
        payload = backups.capture(conn, scope["project_id"])
        backups.validate_payload(conn, payload)
        result = backups.transplant(conn, payload, "portable-live-agent-restored")
        conn.commit()
        restored = backups.capture(conn, result["project_id"])
        backups.validate_payload(conn, restored)
        session = restored["tables"]["app_agent_session"][0]
        self.assertEqual((session["status"], session["restored_read_only"]), ("restored", 1))
        for table, field in (("app_agent_checkpoint", "checkpoint"), ("app_agent_write", "value")):
            self.assertTrue(payload["tables"][table])
            self.assertEqual([row[field] for row in restored["tables"][table]],
                             [row[field] for row in payload["tables"][table]])

    def test_timeline_entities_must_exist_inside_this_archive(self):
        foreign_entity = self.conn.execute("SELECT id FROM entity ORDER BY rowid").fetchone()[0]
        for declaration in (["missing-entity"], [foreign_entity],
                            [self.identities["entity_id"], "missing-entity"],
                            [True], {"id": self.identities["entity_id"]}):
            with self.subTest(declaration=declaration):
                payload = copy.deepcopy(self.payload)
                payload["tables"]["app_timeline_event"][0]["entity_ids_json"] = json_text(
                    declaration
                )
                self.assert_invalid_payload(payload)

    def test_candidate_parent_and_request_hashes_must_match_the_saved_relation(self):
        for field, value in (("parent_hash", "0" * 64), ("request_hash", "0" * 64),
                             ("request_id", "different-request"),
                             ("parent_run_id", "portable-run-failed")):
            with self.subTest(binding_field=field):
                payload = copy.deepcopy(self.payload)
                child = next(row for row in payload["tables"]["app_ai_job"]
                             if row["id"] == "portable-child-job")
                context = json.loads(child["context_json"])
                context["_candidate_revision"][field] = value
                child["context_json"] = json_text(context)
                self.assert_invalid_payload(payload)
        for field in ("candidate_hash", "request_hash"):
            with self.subTest(relation_field=field):
                payload = copy.deepcopy(self.payload)
                payload["tables"]["app_candidate_revision"][0][field] = "0" * 64
                self.assert_invalid_payload(payload)

    def test_explicit_json_source_evidence_and_candidate_parent_references_are_closed(self):
        cases = (
            ("app_ai_job", "portable-job-success", "context_json",
             ("_source", "chapter_id"), "missing-chapter"),
            ("app_ai_job", "portable-job-success", "context_json",
             ("_source", "source_revision_id"), "missing-revision"),
            ("app_ai_job", "portable-job-success", "context_json",
             ("_source", "branch_id"), ALTERNATE_BRANCH),
            ("app_ai_job", "portable-job-success", "context_json",
             ("_source", "chapter_id"), self.identities["alternate_chapter_id"]),
            ("app_ai_job", "portable-job-success", "context_json",
             ("current_chapter", "id"), self.identities["alternate_chapter_id"]),
            ("app_ai_job", "portable-job-success", "result_json",
             ("source", "project_id"), "foreign-book"),
            ("ai_run", "portable-run-success", "consent_json",
             ("source", "source_revision_id"), "missing-revision"),
            ("app_ai_job", "portable-child-job", "context_json",
             ("_candidate_revision", "parent_run_id"), "missing-parent"),
            ("app_ai_job", "portable-child-job", "context_json",
             ("_candidate_revision", "root_run_id"), "missing-root"),
            ("app_ai_job", "portable-job-success", "context_json",
             ("_material_source", "dependencies", 0, "id"), "missing-reference-section"),
            ("app_story_relation", "portable-relation", "evidence_json",
             ("source_span_id",), "missing-span"),
            ("app_story_relation", "portable-relation", "evidence_json",
             ("source_chapter_id",), self.identities["alternate_chapter_id"]),
            ("candidate_memory", "portable-memory", "payload_json",
             ("source_revision_id",), "missing-revision"),
            ("assertion", "portable-assertion", "evidence_json",
             (0, "candidate_memory_id"), "missing-memory"),
            ("app_ai_job", "portable-job-queued", "context_json",
             ("planning_workflow", "id"), "missing-workflow"),
            ("app_ai_job", "portable-job-queued", "context_json",
             ("planning_workflow", "attempt_id"), "portable-plan-attempt"),
            ("app_ai_job", "portable-job-queued", "context_json",
             ("planning_workflow", "stage"), "scenes"),
        )
        for table, identity, column, path, value in cases:
            with self.subTest(table=table, column=column, path=path):
                payload = copy.deepcopy(self.payload)
                row = next(item for item in payload["tables"][table] if item["id"] == identity)
                changed = json.loads(row[column])
                owner = changed
                for field in path[:-1]:
                    owner = owner[field]
                owner[path[-1]] = value
                row[column] = json_text(changed)
                self.assert_invalid_payload(payload)

    def test_historical_restore_receipt_keeps_its_exact_original_payload_bytes(self):
        receipt = {
            "target_scope": {"project_id": SOURCE_PROJECT, "branch_id": SOURCE_BRANCH},
            "request_sha256": text_hash("historical restore request"),
            "result": {
                "project_id": SOURCE_PROJECT, "branch_id": SOURCE_BRANCH,
                "identity_map": {self.identities["chapter_id"]: self.identities["revision_id"]},
                "row_counts": {table: len(rows)
                               for table, rows in self.payload["tables"].items()},
                "rto_seconds": 0.125,
            },
            "archive_sha256": text_hash("historical complete archive"),
            "project_sha256": text_hash("historical project snapshot"),
            "note": "原回执有空白与换行🙂，书内历史不能被重新改写。",
        }
        raw = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        receipt_hash = text_hash(raw)
        event = copy.deepcopy(self.payload_copy["tables"]["event_log"][0])
        event.update(id="portable-historical-receipt",
                     idempotency_key="portable-historical-restore-request",
                     event_type="project.portable_restored", payload_json=raw)
        self.payload_copy["tables"]["event_log"].append(event)
        first = self.restore()
        mapped_id = first["identity_map"][event["id"]]
        restored_event = dict(self.conn.execute(
            "SELECT * FROM event_log WHERE id=?", (mapped_id,),
        ).fetchone())
        self.assertEqual(restored_event["payload_json"].encode("utf-8"), raw.encode("utf-8"))
        self.assertEqual(text_hash(restored_event["payload_json"]), receipt_hash)
        for field in ("id", "project_id", "branch_id", "aggregate_id", "actor_id",
                      "idempotency_key", "expected_head"):
            if event[field] is not None:
                self.assertEqual(restored_event[field], first["identity_map"][event[field]], field)
        captured = backups.capture(self.conn, first["project_id"])
        recaptured_event = next(row for row in captured["tables"]["event_log"]
                                if row["id"] == mapped_id)
        self.assertEqual(recaptured_event["payload_json"], raw)
        second = self.restore(captured, "portable-receipt-second-copy")
        second_raw = self.conn.execute(
            "SELECT payload_json FROM event_log WHERE id=?",
            (second["identity_map"][mapped_id],),
        ).fetchone()[0]
        self.assertEqual(second_raw.encode("utf-8"), raw.encode("utf-8"))
        self.assertEqual(text_hash(second_raw), receipt_hash)

    def test_only_referenced_global_authors_are_captured_and_restored(self):
        scratch = sqlite3.connect(":memory:")
        scratch.row_factory = sqlite3.Row
        self.local.callback(scratch.close)
        source = sqlite3.connect(f"file:{self.source_path}?mode=ro", uri=True)
        try:
            source.backup(scratch)
        finally:
            source.close()
        scratch.execute("PRAGMA foreign_keys=ON")
        referenced, unrelated = "portable-global-history-author", "portable-global-other-author"
        for identity in (referenced, unrelated):
            insert(scratch, "actor", id=identity, project_id=None, kind="user",
                   name="合成全局作者")
        body = self.identities["body"] + "\n另一位作者补充的原始历史。"
        revision_id = "portable-global-author-revision"
        insert(scratch, "document_revision", id=revision_id,
               document_node_id=self.identities["chapter_id"],
               parent_id=self.identities["latest_revision_id"], branch_id=SOURCE_BRANCH,
               content_hash=text_hash(body), canonical_text=body.encode("utf-8"),
               created_at=STAMP, actor_id=referenced, operation="update")
        scratch.commit()
        scratch_before = snapshot(scratch)
        payload = backups.capture(scratch, SOURCE_PROJECT)
        self.assertEqual(snapshot(scratch), scratch_before)
        authors = {row["id"]: row for row in payload["tables"]["actor"]}
        self.assertIn(referenced, authors)
        self.assertIsNone(authors[referenced]["project_id"])
        self.assertNotIn(unrelated, authors)
        with self.subTest(missing_referenced_global_author=True):
            invalid = copy.deepcopy(payload)
            invalid["tables"]["actor"] = [row for row in invalid["tables"]["actor"]
                                         if row["id"] != referenced]
            self.assert_invalid_payload(invalid)
        result = self.restore(payload, "portable-global-author-copy")
        mapping = result["identity_map"]
        self.assertNotIn(unrelated, mapping)
        saved_revision = self.conn.execute(
            "SELECT actor_id,canonical_text FROM document_revision WHERE id=?",
            (mapping[revision_id],),
        ).fetchone()
        self.assertEqual(saved_revision["actor_id"], mapping[referenced])
        self.assertEqual(saved_revision["canonical_text"], body.encode("utf-8"))
        saved_author = self.conn.execute("SELECT * FROM actor WHERE id=?",
                                         (mapping[referenced],)).fetchone()
        self.assertIsNone(saved_author["project_id"])
        captured = backups.capture(self.conn, result["project_id"])
        self.assertIn(mapping[referenced], {row["id"] for row in captured["tables"]["actor"]})
        self.assertNotIn(unrelated, {row["id"] for row in captured["tables"]["actor"]})
        self.assertEqual(snapshot(scratch), scratch_before)

    def test_reference_passages_map_navigation_keep_quotes_and_frozen_request_hashes(self):
        result = self.restore()
        mapping = result["identity_map"]
        source_row = next(row for row in self.payload["tables"]["app_ai_job"]
                          if row["id"] == "portable-job-success")
        original = json.loads(source_row["context_json"])
        current = json.loads(self.conn.execute(
            "SELECT context_json FROM app_ai_job WHERE id=?",
            (mapping["portable-job-success"],),
        ).fetchone()[0])
        old_passage, new_passage = original["reference_passages"][0], current["reference_passages"][0]
        for field in ("document_id", "section_id", "project_id", "branch_id"):
            self.assertEqual(new_passage[field], mapping[old_passage[field]])
        self.assertEqual(new_passage["id"],
                         new_passage["section_id"] + ":0:" + str(len(new_passage["source_quote"])))
        for field in set(old_passage) - {"document_id", "section_id", "project_id", "branch_id", "id"}:
            self.assertEqual(new_passage[field], old_passage[field], field)
        self.assertEqual(current["request_audit"], original["request_audit"])
        source_run = next(row for row in self.payload["tables"]["ai_run"]
                          if row["id"] == "portable-run-success")
        saved_run = self.conn.execute("SELECT * FROM ai_run WHERE id=?",
                                     (mapping["portable-run-success"],)).fetchone()
        self.assertEqual(json.loads(saved_run["consent_json"])["request_audit"],
                         json.loads(source_run["consent_json"])["request_audit"])
        self.assertEqual(saved_run["input_hash"], source_run["input_hash"])
        self.assertEqual(saved_run["prompt_hash"], source_run["prompt_hash"])
        self.assertEqual(current["_material_source"]["dependencies"][0]["sha256"],
                         original["_material_source"]["dependencies"][0]["sha256"])
        self.assertEqual(current["_material_source"]["dependencies"][0]["id"],
                         mapping[original["_material_source"]["dependencies"][0]["id"]])

    def test_reference_passage_missing_parent_cross_scope_quote_and_range_are_rejected(self):
        changes = (
            ("document_id", "missing-reference"), ("section_id", "missing-section"),
            ("project_id", "foreign-book"), ("branch_id", ALTERNATE_BRANCH),
            ("source_quote", "被改动的原句"), ("section_start", True),
            ("section_end", 200000), ("source_start", 1),
            ("source_sha256", "0" * 64), ("section_hash", "0" * 64),
            ("source_hash", "0" * 64),
        )
        for field, value in changes:
            with self.subTest(field=field):
                payload = copy.deepcopy(self.payload)
                row = next(item for item in payload["tables"]["app_ai_job"]
                           if item["id"] == "portable-job-success")
                context = json.loads(row["context_json"])
                context["reference_passages"][0][field] = value
                row["context_json"] = json_text(context)
                self.assert_invalid_payload(payload)

    def test_literal_text_equal_to_old_identity_is_preserved(self):
        result = self.restore()
        mapping, literal = result["identity_map"], self.identities["chapter_id"]
        checks = (
            ("document_node", "title", "id", literal),
            ("app_chapter_meta", "scene_plan", "chapter_id", literal),
            ("app_project_meta", "summary", "project_id", SOURCE_PROJECT),
            ("app_entity_meta", "description", "entity_id", self.identities["entity_id"]),
            ("app_story_relation", "note", "id", "portable-relation"),
            ("app_reference_document", "title", "id", self.identities["reference_id"]),
            ("app_reference_section", "body", "id", self.identities["reference_section_id"]),
            ("app_timeline_event", "title", "id", "portable-timeline"),
            ("app_ai_chunk", "text", "id", "portable-chunk"),
            ("app_ai_chunk", "provider_event_id", "id", "portable-chunk"),
            ("app_ai_job", "instruction", "id", "portable-job-success"),
            ("ai_run", "model", "id", "portable-run-success"),
            ("app_ai_run_decision", "reason", "id", "portable-decision"),
            ("run_manifest", "sanitized_prompt", "id", "portable-run-manifest"),
        )
        for table, field, pk, old_id in checks:
            with self.subTest(table=table, field=field):
                actual = self.conn.execute(
                    f'SELECT "{field}" FROM "{table}" WHERE "{pk}"=?', (mapping[old_id],)
                ).fetchone()[0]
                self.assertEqual(actual, literal)
                self.assertNotEqual(actual, mapping[literal])
        aliases = self.conn.execute("SELECT aliases_json FROM entity WHERE id=?",
                                    (mapping[self.identities["entity_id"]],)).fetchone()[0]
        self.assertEqual(json.loads(aliases), [literal, "作者文字", "chapter-9"])
        settings = json.loads(self.conn.execute(
            "SELECT settings_json FROM app_model_settings WHERE project_id=?",
            (result["project_id"],),
        ).fetchone()[0])
        self.assertEqual(settings["model"], literal)
        self.assertEqual(settings["styles"], {"tone": literal, "instructions": literal})
        self.assertEqual(settings["parameters"]["stop"], [literal])

    def test_canonical_utf8_content_hashes_and_original_source_hashes_are_exact(self):
        cases = (
            ("document_revision", "content_hash", "0" * 64),
            ("document_revision", "canonical_text",
             {"$blob": base64.b64encode(b"changed bytes").decode("ascii")}),
            ("document_revision", "canonical_text", {"$blob": "invalid_base64!"}),
            ("document_revision", "canonical_text",
             {"$blob": base64.b64encode(b"\xff").decode("ascii")}),
            ("app_reference_document", "source_sha256", "0" * 64),
            ("app_reference_document", "text_sha256", "0" * 64),
            ("app_manuscript_import", "source_sha256", "0" * 64),
            ("app_manuscript_import", "text_sha256", "0" * 64),
            ("app_reference_document", "source_bytes",
             {"$blob": base64.b64encode(b"different source").decode("ascii")}),
        )
        for table, field, value in cases:
            with self.subTest(table=table, field=field):
                payload = copy.deepcopy(self.payload)
                payload["tables"][table][0][field] = value
                self.assert_invalid_payload(payload)
        result = self.restore()
        rows = backups.capture(self.conn, result["project_id"])["tables"]
        revisions = {row["id"]: row for row in rows["document_revision"]}
        for original in self.payload["tables"]["document_revision"]:
            actual = revisions[result["identity_map"][original["id"]]]
            self.assertEqual(original["canonical_text"], actual["canonical_text"])
            self.assertEqual(original["content_hash"], actual["content_hash"])
        for table in ("app_manuscript_import", "app_reference_document"):
            actual_by_id = {row["id"]: row for row in rows[table]}
            for original in self.payload["tables"][table]:
                actual = actual_by_id[result["identity_map"][original["id"]]]
                for field in ("source_bytes", "source_sha256", "text_sha256", "metadata_json"):
                    if field.endswith("_json"):
                        self.assertEqual(json.loads(original[field]), json.loads(actual[field]),
                                         (table, field))
                    else:
                        self.assertEqual(original[field], actual[field], (table, field))

    def test_span_range_quote_utf8_offsets_and_reference_section_fingerprints(self):
        cases = (
            ("source_span", "quote", "错误原句"),
            ("source_span", "quote_hash", "0" * 64),
            ("source_span", "start_codepoint", True),
            ("source_span", "start_codepoint", -1),
            ("source_span", "end_codepoint", 2_000_001),
            ("source_span", "start_utf8", 0),
            ("source_span", "end_utf8", 1),
            ("app_reference_section", "body", "原句被改动"),
        )
        for table, field, value in cases:
            with self.subTest(table=table, field=field):
                payload = copy.deepcopy(self.payload)
                payload["tables"][table][0][field] = value
                self.assert_invalid_payload(payload)
        for field, value in (("source_hash", "0" * 64), ("source_start", 1),
                             ("source_end", True)):
            with self.subTest(reference_source_field=field):
                payload = copy.deepcopy(self.payload)
                section = payload["tables"]["app_reference_section"][0]
                source = json.loads(section["source_json"])
                source[field] = value
                section["source_json"] = json_text(source)
                self.assert_invalid_payload(payload)
        # The valid source span deliberately points to a previous revision.
        self.assertNotEqual(self.identities["revision_id"], self.identities["latest_revision_id"])
        result = self.restore()
        span = self.conn.execute("SELECT * FROM source_span WHERE id=?",
                                (result["identity_map"]["portable-span"],)).fetchone()
        self.assertEqual(span["revision_id"], result["identity_map"][self.identities["revision_id"]])
        self.assertEqual(span["quote"], self.payload["tables"]["source_span"][0]["quote"])

    def test_reference_section_origins_match_actual_text_and_epub_members(self):
        for document in (self.identities["reference_id"], self.identities["gb_reference_id"]):
            for field, value in (("preview_chapter_id", "different-preview-chapter"),
                                 ("source_member_hash", "0" * 64)):
                with self.subTest(document=document, origin_field=field):
                    payload = copy.deepcopy(self.payload)
                    section = next(row for row in payload["tables"]["app_reference_section"]
                                   if row["document_id"] == document)
                    source = json.loads(section["source_json"])
                    self.assertTrue(source["sources"])
                    source["sources"][0][field] = value
                    section["source_json"] = json_text(source)
                    self.assert_invalid_payload(payload)
        sys.path.insert(0, str(ROOT / "tests"))
        from manuscript_import_fixtures import epub_bytes
        raw = epub_bytes(first_body="合成EPUB原文件🙂。", second_body="真实来源章节𠀀。")
        parsed = manuscript_import.parse_import(raw, "synthetic-members.epub")
        scope = {"project_id": "portable-target-book", "branch_id": "portable-target-main"}
        reference = manuscript_import.save_reference(
            self.conn, scope, parsed,
            {"mode": "reference", "title": "合成EPUB来源",
             "source_sha256": parsed["source_sha256"]}, raw,
        )
        self.conn.commit()
        payload = backups.capture(self.conn, scope["project_id"])
        sections = [row for row in payload["tables"]["app_reference_section"]
                    if row["document_id"] == reference["id"]]
        with zipfile.ZipFile(io.BytesIO(raw)) as epub:
            for section in sections:
                for origin in json.loads(section["source_json"])["sources"]:
                    self.assertEqual(origin["source_member_hash"],
                                     digest(epub.read(origin["source_member"])))
        for field, value in (("preview_chapter_id", "different-preview-chapter"),
                             ("source_member", "OEBPS/missing.xhtml"),
                             ("source_member_hash", "0" * 64),
                             ("source_member_text_hash", "0" * 64),
                             ("source_start", 1), ("source_end", True)):
            with self.subTest(epub_origin_field=field):
                invalid = copy.deepcopy(payload)
                section = next(row for row in invalid["tables"]["app_reference_section"]
                               if row["id"] == sections[0]["id"])
                source = json.loads(section["source_json"])
                source["sources"][0][field] = value
                section["source_json"] = json_text(source)
                self.assert_invalid_payload(invalid)
        result = self.restore(payload, "portable-epub-origin-copy")
        saved = self.conn.execute(
            "SELECT source_bytes FROM app_reference_document WHERE id=?",
            (result["identity_map"][reference["id"]],),
        ).fetchone()[0]
        self.assertEqual(saved, raw)
        for section in sections:
            saved_source = self.conn.execute(
                "SELECT source_json FROM app_reference_section WHERE id=?",
                (result["identity_map"][section["id"]],),
            ).fetchone()[0]
            self.assertEqual(json.loads(saved_source), json.loads(section["source_json"]))

    def test_model_chunk_and_planning_fingerprints_keep_failed_json_history(self):
        cases = (
            ("ai_run", "portable-run-success", "output_hash", "0" * 64),
            ("app_ai_chunk", "portable-chunk", "seq", 2),
            ("app_ai_chunk", "portable-chunk", "text", "不同的模型分片"),
            ("app_ai_chunk", "portable-chunk", "payload_hash", "0" * 64),
            ("app_ai_chunk", "portable-chunk", "cumulative_hash", "0" * 64),
            ("app_planning_workflow", "portable-workflow", "source_hash", "0" * 64),
            ("app_planning_workflow", "portable-workflow", "source_version", 3),
            ("app_planning_attempt", "portable-plan-attempt", "candidate_hash", "0" * 64),
        )
        for table, identity, field, value in cases:
            with self.subTest(table=table, fingerprint_field=field):
                payload = copy.deepcopy(self.payload)
                next(row for row in payload["tables"][table]
                     if row["id"] == identity)[field] = value
                self.assert_invalid_payload(payload)
        for table, identity, column, field, value in (
                ("app_ai_job", "portable-job-success", "result_json", "text", "改写后的模型结果"),
                ("app_planning_workflow", "portable-workflow", "source_blueprint_json",
                 "premise", "改写后的规划来源"),
                ("app_planning_attempt", "portable-plan-attempt", "candidate_json",
                 "premise", "改写后的规划候选")):
            with self.subTest(table=table, content_column=column):
                payload = copy.deepcopy(self.payload)
                row = next(item for item in payload["tables"][table] if item["id"] == identity)
                value_json = json.loads(row[column])
                value_json[field] = value
                row[column] = json_text(value_json)
                self.assert_invalid_payload(payload)
        structured_payload = copy.deepcopy(self.payload)
        child = next(row for row in structured_payload["tables"]["app_ai_job"]
                     if row["id"] == "portable-child-job")
        child_run = next(row for row in structured_payload["tables"]["ai_run"]
                         if row["id"] == "portable-child-run")
        structured_content = "结构化结果必须来自模型原文🙂。"
        transport = json_text({"content": structured_content})
        result = json.loads(child["result_json"])
        result.update(response_format="json", text=transport,
                      structured={"content": structured_content})
        child["result_json"], child["response_format"] = json_text(result), "json"
        child_run["output_hash"] = text_hash(transport)
        before = snapshot(self.conn)
        backups.validate_payload(self.conn, structured_payload)
        self.assertEqual(snapshot(self.conn), before)
        for case in ("structured-content", "transport-content", "invalid-transport"):
            with self.subTest(structured_case=case):
                invalid = copy.deepcopy(structured_payload)
                job = next(row for row in invalid["tables"]["app_ai_job"]
                           if row["id"] == "portable-child-job")
                run = next(row for row in invalid["tables"]["ai_run"]
                           if row["id"] == "portable-child-run")
                changed = json.loads(job["result_json"])
                if case == "structured-content":
                    changed["structured"]["content"] = "与原文不一致的结构化正文"
                else:
                    changed["text"] = (json_text({"content": "不同的模型原文"})
                                       if case == "transport-content" else '{"content":')
                    run["output_hash"] = text_hash(changed["text"])
                job["result_json"] = json_text(changed)
                self.assert_invalid_payload(invalid)
        saved_result = self.restore(structured_payload, "portable-structured-copy")
        saved_job = self.conn.execute(
            "SELECT result_json FROM app_ai_job WHERE id=?",
            (saved_result["identity_map"]["portable-child-job"],),
        ).fetchone()[0]
        self.assertEqual(json.loads(saved_job)["text"], transport)
        self.assertEqual(json.loads(saved_job)["structured"]["content"], structured_content)
        chunk_payload = copy.deepcopy(self.payload)
        first_chunk = chunk_payload["tables"]["app_ai_chunk"][0]
        second_chunk = copy.deepcopy(first_chunk)
        second_chunk.update(id="portable-second-chunk", seq=2, text="第二个分片🙂。",
                            payload_hash=text_hash("第二个分片🙂。"),
                            cumulative_hash=text_hash(first_chunk["text"] + "第二个分片🙂。"))
        chunk_payload["tables"]["app_ai_chunk"].insert(0, second_chunk)
        backups.validate_payload(self.conn, chunk_payload)
        for field, value in (("seq", 3), ("payload_hash", "0" * 64),
                             ("cumulative_hash", text_hash(second_chunk["text"]))):
            with self.subTest(second_chunk_field=field):
                invalid = copy.deepcopy(chunk_payload)
                invalid["tables"]["app_ai_chunk"][0][field] = value
                self.assert_invalid_payload(invalid)
        restored_chunks = self.restore(chunk_payload, "portable-multiple-chunks-copy")
        rows = self.conn.execute(
            "SELECT seq,text,payload_hash,cumulative_hash FROM app_ai_chunk "
            "WHERE ai_run_id=? ORDER BY seq",
            (restored_chunks["identity_map"]["portable-run-success"],),
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            (chunk["seq"], chunk["text"], chunk["payload_hash"], chunk["cumulative_hash"])
            for chunk in (first_chunk, second_chunk)
        ])
        for index, failed_text in enumerate(('{"content":"截断的旧原文', "旧提供方返回非JSON正文🙂")):
            with self.subTest(historical_failed_json=index):
                payload = copy.deepcopy(self.payload)
                job = next(row for row in payload["tables"]["app_ai_job"]
                           if row["id"] == "portable-job-failed")
                run = next(row for row in payload["tables"]["ai_run"]
                           if row["id"] == "portable-run-failed")
                historical = {
                    "text": failed_text, "response_format": "json",
                    "completion_state": "truncated" if index == 0 else "invalid",
                    "truncated": index == 0, "safe_to_apply": False,
                    "finish_reason": "length" if index == 0 else "stop",
                }
                job["result_json"], job["response_format"] = json_text(historical), "json"
                run.update(output_hash=text_hash(failed_text),
                           input_hash=text_hash("historic unresolved input hash"),
                           prompt_hash=text_hash("historic unresolved prompt hash"))
                restored = self.restore(payload, "portable-failed-json-copy-" + str(index))
                saved = self.conn.execute(
                    "SELECT result_json FROM app_ai_job WHERE id=?",
                    (restored["identity_map"][job["id"]],),
                ).fetchone()[0]
                self.assertEqual(json.loads(saved), historical)
                saved_run = self.conn.execute("SELECT * FROM ai_run WHERE id=?",
                                              (restored["identity_map"][run["id"]],)).fetchone()
                for field in ("output_hash", "input_hash", "prompt_hash"):
                    self.assertEqual(saved_run[field], run[field], field)

    def test_no_active_branch_is_rejected_without_partial_insert(self):
        for branch in self.payload_copy["tables"]["branch"]:
            branch["status"] = "archived"
        self.assert_invalid_payload(self.payload_copy)

    def test_mid_insert_trigger_failure_rolls_back_all_target_rows_in_outer_transaction(self):
        self.conn.execute("""
            CREATE TRIGGER portable_insert_failure BEFORE INSERT ON app_ai_chunk
            WHEN NEW.project_id='portable-trigger-copy'
            BEGIN SELECT RAISE(ABORT,'synthetic mid-restore failure'); END
        """)
        self.conn.commit()
        before = snapshot(self.conn)
        self.conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            backups.transplant(self.conn, self.payload_copy, "portable-trigger-copy")
        self.assertEqual(snapshot(self.conn), before,
                         "caller has not rolled back: transplant must be atomic")
        self.assertTrue(self.conn.in_transaction, "caller transaction must stay open")
        self.conn.commit()
        self.assertEqual(snapshot(self.conn), before)

    def test_mid_insert_failure_preserves_the_callers_pending_changes(self):
        self.conn.execute("""
            CREATE TRIGGER portable_pending_failure BEFORE INSERT ON app_ai_chunk
            WHEN NEW.project_id='portable-pending-copy'
            BEGIN SELECT RAISE(ABORT,'synthetic pending restore failure'); END
        """)
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute(
            "UPDATE app_project_meta SET summary=? WHERE project_id=?",
            ("caller has an unrelated pending edit", "portable-target-book"),
        )
        before = snapshot(self.conn)
        deferred = self.conn.execute("PRAGMA defer_foreign_keys").fetchone()[0]
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            backups.transplant(self.conn, self.payload_copy, "portable-pending-copy")
        self.assertEqual(snapshot(self.conn), before)
        self.assertEqual(self.conn.execute("PRAGMA defer_foreign_keys").fetchone()[0], deferred)
        self.assertTrue(self.conn.in_transaction)
        self.conn.commit()
        with sqlite3.connect(self.target_path) as reopened:
            self.assertEqual(snapshot(reopened), before)

    def test_mid_insert_failure_without_a_caller_transaction_writes_nothing(self):
        self.conn.execute("""
            CREATE TRIGGER portable_standalone_failure BEFORE INSERT ON app_ai_chunk
            WHEN NEW.project_id='portable-standalone-copy'
            BEGIN SELECT RAISE(ABORT,'synthetic standalone restore failure'); END
        """)
        self.conn.commit()
        before = snapshot(self.conn)
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            backups.transplant(self.conn, self.payload_copy, "portable-standalone-copy")
        self.assertEqual(snapshot(self.conn), before)
        self.conn.commit()
        with sqlite3.connect(self.target_path) as reopened:
            self.assertEqual(snapshot(reopened), before)

    def test_restored_jobs_runs_and_planning_remain_readable_without_dispatch(self):
        result = self.restore()
        mapping = result["identity_map"]
        with workspace.use_scope(result["project_id"], result["branch_id"],
                                 mapping[SOURCE_ACTOR]):
            for original in self.payload["tables"]["app_ai_job"]:
                job_id, run_id = mapping[original["id"]], mapping[original["ai_run_id"]]
                job = self.conn.execute("SELECT * FROM app_ai_job WHERE id=?", (job_id,)).fetchone()
                run = self.conn.execute("SELECT * FROM ai_run WHERE id=?", (run_id,)).fetchone()
                self.assertTrue(json.loads(job["context_json"])["_restored_read_only"])
                self.assertTrue(json.loads(run["consent_json"])["restored_read_only"])
                public = server.ai_job_public(job, conn=self.conn)
                self.assertTrue(public["restored_read_only"])
                self.assertNotEqual(job["status"], "queued")
                self.assertNotEqual(job["status"], "running")
                for field in ("input_hash", "prompt_hash", "output_hash",
                              "token_json", "cost_json"):
                    source_run = next(row for row in self.payload["tables"]["ai_run"]
                                      if row["id"] == original["ai_run_id"])
                    if field.endswith("_json"):
                        self.assertEqual(json.loads(run[field]), json.loads(source_run[field]))
                    else:
                        self.assertEqual(run[field], source_run[field])
                before = snapshot(self.conn)
                self.conn.commit()
                server.process_ai_job(job_id)
                self.assertEqual(snapshot(self.conn), before)
                with self.assertRaisesRegex((RuntimeError, ValueError), "仅供阅读"):
                    server.retry_ai_job(self.conn, job_id, {"mode": "original"})
                self.conn.rollback()
                self.assertEqual(snapshot(self.conn), before)
                with self.assertRaisesRegex(ValueError, "仅供阅读"):
                    server.revise_ai_candidate(self.conn, run_id, {
                        "request_id": "new-synthetic-request",
                        "candidate_hash": text_hash(self.identities["chapter_id"]),
                        "notes": "",
                    })
                self.conn.rollback()
                self.assertEqual(snapshot(self.conn), before)
            callback = Mock(side_effect=AssertionError("planning dispatch forbidden"))
            for workflow in self.payload["tables"]["app_planning_workflow"]:
                workflow_id = mapping[workflow["id"]]
                read = planning.get_workflow(self.conn, workflow_id)
                self.assertEqual(read["status"], "canceled")
                before = snapshot(self.conn)
                with self.assertRaises(planning.PlanningConflict):
                    planning.start_stage(self.conn, workflow_id, {"create_job": callback})
                self.conn.rollback()
                self.assertEqual(snapshot(self.conn), before)
            callback.assert_not_called()

    def test_inline_legacy_credentials_are_removed_from_restore_and_new_capture(self):
        row = self.payload_copy["tables"]["app_model_settings"][0]
        original = json.loads(row["settings_json"])
        original.update(api_key=SYNTHETIC_CREDENTIAL, token=SYNTHETIC_CREDENTIAL,
                        authorization=SYNTHETIC_CREDENTIAL,
                        _credential_ref="synthetic-old-reference",
                        last_probe={"success": True, "private": SYNTHETIC_CREDENTIAL})
        row["settings_json"] = json_text(original)
        result = self.restore()
        saved = json.loads(self.conn.execute(
            "SELECT settings_json FROM app_model_settings WHERE project_id=?",
            (result["project_id"],),
        ).fetchone()[0])
        self.assertNotIn(SYNTHETIC_CREDENTIAL, json_text(saved))
        self.assertNotIn("api_key", saved)
        self.assertIsNone(saved.get("_credential_ref"))
        self.assertIsNone(saved.get("last_probe"))
        for field in ("model", "models", "styles", "parameters", "daily_limit"):
            self.assertEqual(saved[field], original[field])
        recaptured = backups.capture(self.conn, result["project_id"])
        self.assertNotIn(SYNTHETIC_CREDENTIAL, json_text(recaptured))


class EvidenceResult(unittest.TextTestResult):
    """Report actual subtest executions without calling them product scenarios."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subtests = 0
        self.subtest_failures = 0

    def addSubTest(self, test, subtest, error):
        self.subtests += 1
        self.subtest_failures += error is not None
        super().addSubTest(test, subtest, error)


def main() -> int:
    report_path = os.environ.get("MOYU_PORTABLE_BACKUP_REPORT")
    report_target = Path(report_path) if report_path else None
    if report_target and report_target.exists():
        raise RuntimeError("Evidence exists; use a new portable backup report path")
    files = (
        "moyu_backups.py", "server.py", "moyu_workspace.py", "moyu_planning.py",
        "moyu_agent_checkpoint.py", "moyu_agent_service.py", "moyu_writing_agent.py", "requirements.txt",
        "moyu_settings.py", "moyu_records.py", "moyu_import.py", "moyu_analysis.py",
        "docs/schema-v1.sql", "docs/schema-v1.1-migration.sql",
        "tests/portable_backup_fixtures.py", "tests/manuscript_import_fixtures.py",
    )
    before = {name: digest((ROOT / name).read_bytes()) for name in files}
    started = datetime.now(timezone.utc).isoformat()
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PortableBackupTests)
    )
    after = {name: digest((ROOT / name).read_bytes()) for name in files}
    report = {
        "passed": result.wasSuccessful() and before == after,
        "tests": result.testsRun,
        "executed_subtests": result.subtests,
        "failed_subtests": result.subtest_failures,
        "captured_tables": len(ALL_TABLES),
        "provider_calls": 0,
        "worker_calls": 0,
        "paid_provider_calls": 0,
        "real_database_access": False,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_files_sha256_before": before,
        "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "synthetic_source_database_sha256_before": getattr(
            PortableBackupTests, "source_file_hash", None),
        "synthetic_source_database_sha256_after": getattr(
            PortableBackupTests, "source_file_hash_after", None),
        "scope": "temporary synthetic databases; strict portable complete-ZIP helpers",
        "claim_limit": "helper contracts only; not HTTP, browser, literary quality, or competitiveness",
    }
    if report_target:
        report_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    print(json.dumps({name: value for name, value in report.items()
                      if name not in {"source_files_sha256_before", "source_files_sha256_after"}},
                     ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
