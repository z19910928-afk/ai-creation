#!/usr/bin/env python3
"""HTTP import and complete-project archive lifecycle fixtures.

This is intentionally an executable fixture rather than a unit-test-only
module.  It talks to the real local HTTP handler, uses an isolated SQLite
database, and keeps every source byte needed to prove import and recovery
round-trips.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import moyu_analysis
import moyu_backups
import server


def _request(base: str, method: str, path: str, payload=None, project: str | None = None,
             branch: str | None = None):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if project:
        headers["X-Moyu-Project"] = project
    if branch:
        headers["X-Moyu-Branch"] = branch
    request = urllib.request.Request(base + path, data=raw, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content = response.read()
            return response.status, json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        content = exc.read()
        try:
            body = json.loads(content)
        except json.JSONDecodeError:
            body = {"raw": content.decode("utf-8", errors="replace")}
        return exc.code, body


def _post_import_preview(base: str, filename: str, raw: bytes, project: str | None = None):
    return _request(
        base,
        "POST",
        "/api/import/preview",
        {"filename": filename, "data_base64": base64.b64encode(raw).decode("ascii")},
        project,
    )


def _apply_import(base: str, preview: dict, *, mode: str, title: str,
                  chapters=None, project: str | None = None, source_sha256=None):
    data = {
        "preview_id": preview["preview_id"],
        "source_sha256": source_sha256 or preview["source_sha256"],
        "mode": mode,
        "title": title,
    }
    if chapters is not None:
        data["chapters"] = chapters
    return _request(base, "POST", "/api/import/apply", data, project)


def _assert_status(status: int, body: dict, expected: int | set[int]):
    allowed = {expected} if isinstance(expected, int) else set(expected)
    assert status in allowed, (status, body)


def _json_remap(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_json_remap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(key, key): _json_remap(item, mapping) for key, item in value.items()}
    return value


def _decode_backup_value(value):
    if isinstance(value, dict) and set(value) == {"$blob"}:
        return base64.b64decode(value["$blob"], validate=True)
    return value


def _expected_restored_row(table: str, row: dict, mapping: dict):
    expected = {}
    for key, value in row.items():
        value = _decode_backup_value(value)
        if key.endswith("_json") and isinstance(value, str):
            value = json.dumps(_json_remap(json.loads(value), mapping), ensure_ascii=False)
        elif isinstance(value, str):
            value = mapping.get(value, value)
        expected[key] = value
    if table == "project":
        expected["name"] = expected["name"] + " · 恢复副本"
    if table == "app_ai_job" and row["status"] in {"queued", "running"}:
        expected.update(
            status="failed",
            error="从备份恢复；旧请求不会自动再次调用模型",
            cancel_requested=1,
        )
    if table == "ai_run" and row["status"] in {"queued", "running"}:
        expected.update(status="unknown", error="备份中的外部请求状态未知，未重复执行")
    return expected


def _table_rows(conn: sqlite3.Connection, table: str):
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"').fetchall()]


def _compare_archive_tables(conn: sqlite3.Connection, payload: dict, restored_project: str,
                            identity_map: dict):
    """Compare every captured table after applying transplant's documented changes."""
    for table, source_rows in payload["tables"].items():
        expected = [_expected_restored_row(table, row, identity_map) for row in source_rows]
        actual = _table_rows(conn, table)
        schema = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        columns = {row[1] for row in schema}
        primary_key = [row[1] for row in sorted(schema, key=lambda row: row[5]) if row[5]]
        if "project_id" in columns:
            actual = [row for row in actual if row.get("project_id") == restored_project]
        elif table == "project":
            actual = [row for row in actual if row.get("id") == restored_project]
        else:
            expected_keys = {tuple(row[key] for key in primary_key) for row in expected}
            actual = [row for row in actual if tuple(row[key] for key in primary_key) in expected_keys]
        if table == "event_log":
            assert len(actual) == len(expected) + 1, (table, len(actual), len(expected))
            additions = [row for row in actual if row["event_type"] == "project.restored"]
            assert len(additions) == 1
            actual = [row for row in actual if row["id"] != additions[0]["id"]]
        elif table == "app_state_version":
            for item in expected:
                item["version"] += 1
        assert len(actual) == len(expected), (table, len(actual), len(expected))
        actual_counts = {}
        for row in actual:
            key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=lambda item: item.hex() if isinstance(item, bytes) else item)
            actual_counts[key] = actual_counts.get(key, 0) + 1
        for row in expected:
            key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=lambda item: item.hex() if isinstance(item, bytes) else item)
            assert actual_counts.get(key, 0), (table, row)
            actual_counts[key] -= 1


def _seed_complex_rows(source_project: str, branch_id: str, actor_id: str):
    """Populate durable tables that the complete archive must preserve."""
    with server.connect() as conn:
        chapter = conn.execute(
            "SELECT id,revision_id,canonical_text FROM ("
            "SELECT n.id,r.id revision_id,r.canonical_text,"
            "ROW_NUMBER() OVER (ORDER BY n.ordinal,n.rowid) number "
            "FROM document_node n JOIN document_revision r ON r.id=("
            "SELECT rr.id FROM document_revision rr WHERE rr.document_node_id=n.id "
            "ORDER BY rr.rowid DESC LIMIT 1) "
            "WHERE n.project_id=? AND n.branch_id=? AND n.kind='chapter' "
            "AND NOT EXISTS (SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id)) "
            "WHERE number=1",
            (source_project, branch_id),
        ).fetchone()
        assert chapter is not None
        body = bytes(chapter["canonical_text"]).decode("utf-8")
        quote = body[: min(len(body), 12)] or "正文"
        entity_id = "ent_archive_knowledge"
        conn.execute(
            "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status) VALUES(?,?,?,?,?,?)",
            (entity_id, source_project, "人物", "归档人物", json.dumps(["归档"], ensure_ascii=False), "active"),
        )
        conn.execute(
            "INSERT INTO app_entity_meta(entity_id,description,updated_at) VALUES(?,?,?)",
            (entity_id, "完整备份中的人物资料", server.now()),
        )
        span_id = "span_archive_quote"
        conn.execute(
            "INSERT INTO source_span(id,project_id,revision_id,document_node_id,start_codepoint,end_codepoint,"
            "start_utf8,end_utf8,quote_hash,quote,anchor_before,anchor_after,status,external_uri,snapshot_hash,snapshot_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                span_id,
                source_project,
                chapter["revision_id"],
                chapter["id"],
                0,
                len(quote),
                0,
                len(quote.encode("utf-8")),
                server.hash_text(quote),
                quote,
                "",
                "",
                "valid",
                "offline://source",
                server.hash_text(quote),
                server.now(),
            ),
        )
        memory_id = "mem_archive_fact"
        conn.execute(
            "INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,entity_id,kind,payload_json,"
            "status,confidence,created_at,reviewed_at,reviewed_by,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                memory_id,
                source_project,
                branch_id,
                span_id,
                entity_id,
                "人物",
                json.dumps(
                    {
                        "key": "archive.fact",
                        "name": "归档人物",
                        "source": "完整备份夹具",
                        "source_chapter_id": chapter["id"],
                        "source_quote": quote,
                        "source_start": 0,
                        "source_end": len(quote),
                        "source_hash": server.hash_text(quote),
                    },
                    ensure_ascii=False,
                ),
                "accepted",
                0.75,
                server.now(),
                server.now(),
                actor_id,
                actor_id,
            ),
        )
        assertion_id = "assert_archive_fact"
        conn.execute(
            "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
            "epistemic_mode,state,valid_start,valid_end,confidence,supersedes_id,evidence_json,reviewed_by,reviewed_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                assertion_id,
                source_project,
                branch_id,
                entity_id,
                "拥有线索",
                json.dumps({"value": "旧罗盘"}, ensure_ascii=False),
                "world_truth",
                "canon",
                None,
                None,
                0.7,
                None,
                json.dumps([{"candidate_memory_id": memory_id, "source_span_id": span_id}], ensure_ascii=False),
                actor_id,
                server.now(),
            ),
        )
        conn.execute(
            "INSERT INTO character_state(id,project_id,branch_id,entity_id,attribute,value_json,event_id,"
            "valid_start,valid_end,knowledge_scope_json,source_assertion_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "char_state_archive",
                source_project,
                branch_id,
                entity_id,
                "状态",
                json.dumps({"value": "警觉"}, ensure_ascii=False),
                None,
                None,
                None,
                json.dumps({"known_by": ["归档人物"]}, ensure_ascii=False),
                assertion_id,
            ),
        )
        second_entity = conn.execute(
            "SELECT id FROM entity WHERE project_id=? AND id<>? ORDER BY rowid LIMIT 1",
            (source_project, entity_id),
        ).fetchone()
        assert second_entity is not None
        relation_id = "relation_archive"
        conn.execute(
            "INSERT INTO app_story_relation(id,project_id,branch_id,from_entity_id,to_entity_id,relation_type,note,status,"
            "created_at,updated_at,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                relation_id,
                source_project,
                branch_id,
                entity_id,
                second_entity["id"],
                "认识",
                "完整备份关系",
                "active",
                server.now(),
                server.now(),
                json.dumps({"source_span_id": span_id}, ensure_ascii=False),
            ),
        )
        conn.execute(
            "INSERT INTO app_timeline_event(id,project_id,branch_id,title,event_time,chapter_id,entity_ids_json,"
            "summary,status,ordinal,created_at,updated_at,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "timeline_archive",
                source_project,
                branch_id,
                "归档事件",
                "第一章",
                chapter["id"],
                json.dumps([entity_id], ensure_ascii=False),
                "完整备份时间线",
                "planned",
                1,
                server.now(),
                server.now(),
                json.dumps({"source_span_id": span_id}, ensure_ascii=False),
            ),
        )
        plot_id = "plot_archive"
        conn.execute(
            "INSERT INTO app_plot_item(id,project_id,branch_id,stage,text,created_at,updated_at,archived)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (plot_id, source_project, branch_id, "setup", "归档伏笔", server.now(), server.now(), 0),
        )
        conn.execute(
            "INSERT INTO app_foreshadow_link(id,project_id,branch_id,plot_id,chapter_id,kind,note,created_at,updated_at,evidence_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "foreshadow_archive",
                source_project,
                branch_id,
                plot_id,
                chapter["id"],
                "plant",
                "备份伏笔",
                server.now(),
                server.now(),
                json.dumps({"source_span_id": span_id}, ensure_ascii=False),
            ),
        )
        scope = {"project_id": source_project, "branch_id": branch_id, "actor_id": actor_id}
        chapter_input = {"id": chapter["id"], "body": body, "revision_id": chapter["revision_id"]}
        moyu_analysis.save_analysis(
            conn,
            scope,
            chapter_input,
            None,
            {
                "summary": {"text": "归档分析", "evidence": [{"source_quote": quote}]},
                "handoff": {"text": "归档交接", "evidence": [{"source_quote": quote}]},
                "candidates": [],
            },
        )
        moyu_analysis.save_review(
            conn,
            scope,
            chapter_input,
            None,
            {
                "findings": [{
                    "category": "style",
                    "severity": "suggestion",
                    "message": "归档审稿建议。",
                    "evidence": [{"source_quote": quote}],
                }],
                "uncertainties": [],
            },
        )
        stamp = server.now()
        for suffix, status in (("queued", "queued"), ("running", "running")):
            run_id = f"run_archive_{suffix}"
            conn.execute(
                "INSERT INTO ai_run(id,project_id,branch_id,mode,provider,model,model_snapshot,system_prompt_hash,"
                "prompt_hash,input_hash,output_hash,sampling_json,tool_calls_json,seed,started_at,ended_at,status,error,"
                "context_item_ids_json,token_json,latency_ms,cost_json,author_decision,consent_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    source_project,
                    branch_id,
                    "continue",
                    "offline",
                    "offline-test",
                    "offline-test",
                    "system",
                    "prompt",
                    "input",
                    None,
                    "{}",
                    "[]",
                    None,
                    stamp,
                    None,
                    status,
                    None,
                    "[]",
                    "{}",
                    None,
                    "{}",
                    "pending",
                    "{}",
                ),
            )
            conn.execute(
                "INSERT INTO app_ai_job(id,project_id,branch_id,ai_run_id,task,chapter_id,instruction,context_json,"
                "response_format,max_tokens,status,attempt,retry_of,cancel_requested,error,result_json,created_at,"
                "started_at,finished_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"job_archive_{suffix}",
                    source_project,
                    branch_id,
                    run_id,
                    "continue",
                    chapter["id"],
                    "离线归档任务",
                    "{}",
                    "text",
                    100,
                    status,
                    0,
                    None,
                    0,
                    None,
                    None,
                    stamp,
                    stamp if status == "running" else None,
                    None,
                    stamp,
                ),
            )
        conn.execute(
            "INSERT INTO audit_event(id,project_id,actor_id,action,target_id,before_hash,after_hash,reason,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "audit_archive",
                source_project,
                actor_id,
                "archive.fixture",
                chapter["id"],
                "before",
                "after",
                "完整备份审计",
                stamp,
            ),
        )
        conn.commit()
        return {
            "chapter_id": chapter["id"],
            "revision_id": chapter["revision_id"],
            "body": body,
            "entity_id": entity_id,
            "span_id": span_id,
        }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="moyu-import-archive-") as temp:
        temp_root = Path(temp)
        server.DB_PATH = temp_root / "book.sqlite3"
        server._IMPORT_PREVIEWS.clear()
        server.init_db()
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{http.server_port}"
        try:
            status, initial = _request(base, "GET", "/api/state")
            _assert_status(status, initial, 200)
            root_project = initial["project"]["id"]
            root_count = len(_request(base, "GET", "/api/projects")[1]["projects"])

            text = (
                "\ufeff序言：潮声留在门外。\r\n\r\n"
                "第一章 夜潮\r\n林岚握住红铜罗盘，𠀀字和🙂都没有丢。\r\n\r\n"
                "第二章 北塔\r\n守灯人说：“别删掉这句。”\r\n"
            )
            raw_utf8 = text.encode("utf-8")
            status, preview = _post_import_preview(base, "夜航.md", raw_utf8)
            _assert_status(status, preview, 200)
            assert preview["metadata"]["encoding"] == "utf-8"
            assert preview["metadata"]["exact_decoded_text"] is True
            assert "".join(item["body"] for item in preview["chapters"]) == text
            assert preview["source_sha256"] == hashlib.sha256(raw_utf8).hexdigest()

            # A stale digest and a missing/changed codepoint must be rejected
            # before any project is created.
            bad_digest = _apply_import(base, preview, mode="manuscript", title="错误摘要", source_sha256="0" * 64)
            _assert_status(bad_digest[0], bad_digest[1], {400, 409})
            midpoint = text.index("红铜")
            missing = _apply_import(
                base,
                preview,
                mode="manuscript",
                title="缺字",
                chapters=[{"title": "少一字", "body": text[:-1]}],
            )
            _assert_status(missing[0], missing[1], {400, 409})

            # Splitting/merging and renaming chapter boundaries preserves every
            # source codepoint, including a CRLF and non-BMP characters.
            selected = [
                {"title": "前言（改名）", "body": text[:midpoint]},
                {"title": "正文合章（改名）", "body": text[midpoint:]},
            ]
            other_project = _request(base, "POST", "/api/projects", {"title": "隔离作品"})[1]["project"]["id"]
            wrong_scope = _apply_import(
                base, preview, mode="manuscript", title="错误作用域", chapters=selected, project=other_project
            )
            _assert_status(wrong_scope[0], wrong_scope[1], {400, 404, 409})
            status, imported = _apply_import(
                base, preview, mode="manuscript", title="夜航旧稿", chapters=selected
            )
            _assert_status(status, imported, 201)
            manuscript_project = imported["project"]["id"]
            manuscript_state = imported["state"]
            assert [chapter["body"] for chapter in manuscript_state["chapters"]] == [text[:midpoint], text[midpoint:]]
            assert [chapter["title"] for chapter in manuscript_state["chapters"]] == ["前言（改名）", "正文合章（改名）"]
            after_first_apply = len(_request(base, "GET", "/api/projects")[1]["projects"])
            status, repeated = _apply_import(
                base, preview, mode="manuscript", title="夜航旧稿", chapters=selected
            )
            _assert_status(status, repeated, {200, 201})
            assert repeated.get("idempotent") is True
            assert len(_request(base, "GET", "/api/projects")[1]["projects"]) == after_first_apply

            with server.connect() as conn:
                row = conn.execute(
                    "SELECT source_bytes,source_sha256 FROM app_manuscript_import WHERE project_id=?",
                    (manuscript_project,),
                ).fetchone()
                assert row is not None and bytes(row["source_bytes"]) == raw_utf8
                assert row["source_sha256"] == hashlib.sha256(raw_utf8).hexdigest()

            # Import a GB18030 reference into the manuscript project.  It is
            # searchable only through the reference scope and never appears as
            # story chapters or canon memory.
            manuscript_state = _request(base, "GET", "/api/state", project=manuscript_project)[1]
            reference_text = "资料序\n北塔旧档案：红铜罗盘属于守灯人。\n第一章 旧案\n原件中的𠀀字与中文。\n"
            raw_gb = reference_text.encode("gb18030", errors="strict")
            status, ref_preview = _post_import_preview(base, "旧档案.txt", raw_gb, manuscript_project)
            _assert_status(status, ref_preview, 200)
            assert ref_preview["metadata"]["encoding"] == "gb18030"
            assert "".join(item["body"] for item in ref_preview["chapters"]) == reference_text
            status, reference = _apply_import(
                base,
                ref_preview,
                mode="reference",
                title="旧档案资料",
                project=manuscript_project,
            )
            _assert_status(status, reference, 201)
            reference_id = reference["reference"]["id"]
            status, duplicate_reference = _apply_import(
                base,
                ref_preview,
                mode="reference",
                title="旧档案资料",
                project=manuscript_project,
            )
            _assert_status(status, duplicate_reference, {200, 201})
            assert duplicate_reference.get("idempotent") is True
            assert duplicate_reference["reference"]["id"] == reference_id
            status, details = _request(base, "GET", f"/api/references/{reference_id}", project=manuscript_project)
            _assert_status(status, details, 200)
            assert all(item["material_kind"] == "reference" and item["use"] == "reference_not_story_fact" for item in details["chapters"])
            assert "source_bytes" not in details
            status, search = _request(
                base,
                "GET",
                f"/api/references/{reference_id}/search?q={urllib.parse.quote('红铜罗盘')}",
                project=manuscript_project,
            )
            _assert_status(status, search, 200)
            assert search["results"] and all(item["material_kind"] == "reference" for item in search["results"])
            assert _request(base, "GET", f"/api/references/{reference_id}", project=other_project)[0] == 404
            status, other_search = _request(
                base,
                "GET",
                f"/api/references/{reference_id}/search?q={urllib.parse.quote('红铜罗盘')}",
                project=other_project,
            )
            _assert_status(status, other_search, 200)
            assert other_search["results"] == []
            with server.connect() as conn:
                conn.execute(
                    "INSERT INTO branch VALUES(?,?,?,?,?,?,?)",
                    ("archive_alternate", manuscript_project, None, None, None, "active", "{}"),
                )
            assert _request(
                base,
                "GET",
                f"/api/references/{reference_id}",
                project=manuscript_project,
                branch="archive_alternate",
            )[0] == 404
            status, branch_search = _request(
                base,
                "GET",
                f"/api/references/{reference_id}/search?q={urllib.parse.quote('红铜罗盘')}",
                project=manuscript_project,
                branch="archive_alternate",
            )
            _assert_status(status, branch_search, 200)
            assert branch_search["results"] == []
            status, alternate_references = _request(
                base,
                "GET",
                "/api/references",
                project=manuscript_project,
                branch="archive_alternate",
            )
            _assert_status(status, alternate_references, 200)
            assert alternate_references["references"] == []
            manuscript_state_after_ref = _request(base, "GET", "/api/state", project=manuscript_project)[1]
            for group in ("project", "chapters", "records", "memory", "plot"):
                assert manuscript_state_after_ref[group] == manuscript_state[group], group
            assert reference_text not in "\n".join(chapter["body"] for chapter in manuscript_state_after_ref["chapters"])
            assert not any(memory.get("name") == "守灯人" for memory in manuscript_state_after_ref["memory"])
            with server.connect() as conn:
                row = conn.execute(
                    "SELECT source_bytes FROM app_reference_document WHERE id=?", (reference_id,)
                ).fetchone()
                assert row is not None and bytes(row["source_bytes"]) == raw_gb

            # A failed sync must roll back the just-created project, chapters,
            # original bytes, and event rows as one transaction.
            rollback_text = "第一章 回滚测试\n这本书不应留下半本。\n"
            status, rollback_preview = _post_import_preview(base, "rollback.txt", rollback_text.encode())
            _assert_status(status, rollback_preview, 200)
            before_projects = _request(base, "GET", "/api/projects")[1]["projects"]
            with server.connect() as conn:
                before_tables = {
                    table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    for table in moyu_backups.tables(conn)
                }
            with patch("server.sync_state", side_effect=RuntimeError("forced sync failure")):
                status, failed = _apply_import(base, rollback_preview, mode="manuscript", title="失败导入")
            assert status >= 500, failed
            after_projects = _request(base, "GET", "/api/projects")[1]["projects"]
            assert [item["id"] for item in after_projects] == [item["id"] for item in before_projects]
            with server.connect() as conn:
                for table, count in before_tables.items():
                    assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == count, table
                assert conn.execute("SELECT COUNT(*) FROM project WHERE name='失败导入'").fetchone()[0] == 0

            # Give the manuscript a second durable revision before archiving.
            changed_state = copy.deepcopy(manuscript_state_after_ref)
            changed_state["chapters"][0]["body"] += "\n修订行：保留原稿与新稿。"
            status, updated = _request(base, "PUT", "/api/state", changed_state, manuscript_project)
            _assert_status(status, updated, 200)
            with server.connect() as conn:
                actor = conn.execute("SELECT id FROM actor WHERE project_id=? ORDER BY rowid LIMIT 1", (manuscript_project,)).fetchone()["id"]
            _seed_complex_rows(manuscript_project, manuscript_state_after_ref["branch_id"], actor)

            status, backup = _request(base, "POST", "/api/backups", {}, manuscript_project)
            _assert_status(status, backup, 201)
            assert backup["verified"] is True
            backup_id = backup["id"]
            package = temp_root / "backups" / f"{backup_id}.zip"
            archive = moyu_backups.read(package, manuscript_project)
            tables = archive["payload"]["tables"]
            required_tables = {
                "app_reference_document",
                "app_reference_section",
                "document_node",
                "document_revision",
                "source_span",
                "entity",
                "candidate_memory",
                "assertion",
                "character_state",
                "app_story_relation",
                "app_timeline_event",
                "app_foreshadow_link",
                "app_chapter_analysis",
                "app_story_review",
                "app_ai_job",
                "audit_event",
            }
            assert required_tables <= set(tables), (required_tables - set(tables), set(tables))
            assert all(tables[name] for name in required_tables)
            assert any(row["source_sha256"] == hashlib.sha256(raw_gb).hexdigest() for row in tables["app_reference_document"])
            source_event_keys = {row["idempotency_key"] for row in tables["event_log"]}

            with patch("server.call_ai_provider", side_effect=AssertionError("archive must not call a provider")) as provider:
                with patch("server.start_ai_worker", side_effect=AssertionError("archive must not restart a job")) as worker:
                    status, restored = _request(
                        base, "POST", f"/api/backups/{backup_id}/restore", {"confirm": True}, manuscript_project
                    )
                assert not provider.called and not worker.called
            _assert_status(status, restored, 201)
            restored_project = restored["project"]["id"]
            assert restored_project != manuscript_project
            assert restored["state"]["chapters"][0]["body"] == updated["chapters"][0]["body"]
            with server.connect() as conn:
                assert moyu_backups.capture(conn, manuscript_project) == archive["payload"]
                _compare_archive_tables(
                    conn,
                    archive["payload"],
                    restored_project,
                    restored["identity_map"],
                )
                assert not conn.execute("PRAGMA foreign_key_check").fetchall()
                assert all(
                    restored["identity_map"][row["id"]] != row["id"]
                    for rows in tables.values()
                    for row in rows
                    if row.get("id")
                )
                restored_keys = {
                    row["idempotency_key"]
                    for row in conn.execute("SELECT idempotency_key FROM event_log WHERE project_id=?", (restored_project,))
                }
                assert source_event_keys.isdisjoint(restored_keys)
                restored_jobs = conn.execute(
                    "SELECT j.status job_status,r.status run_status,j.error,j.cancel_requested FROM app_ai_job j "
                    "JOIN ai_run r ON r.id=j.ai_run_id WHERE j.project_id=? ORDER BY j.id",
                    (restored_project,),
                ).fetchall()
                assert len(restored_jobs) == 2
                assert all(row["job_status"] == "failed" and row["cancel_requested"] == 1 and row["error"] for row in restored_jobs)
                assert all(row["run_status"] == "unknown" for row in restored_jobs)
                restored_ref = conn.execute(
                    "SELECT source_bytes FROM app_reference_document WHERE project_id=?",
                    (restored_project,),
                ).fetchone()
                assert restored_ref is not None and bytes(restored_ref["source_bytes"]) == raw_gb
            with patch("server.generate_ai", side_effect=AssertionError("restored jobs must not run")) as generate:
                status, drained = _request(base, "POST", "/api/ai/queue/drain", {}, restored_project)
                _assert_status(status, drained, 200)
                assert not generate.called
            assert _request(
                base, "GET", "/api/ai/queue?status=queued", project=restored_project
            )[1]["jobs"] == []

            # Mutating the recovered copy must leave the source manuscript intact.
            recovered_state = _request(base, "GET", "/api/state", project=restored_project)[1]
            recovered_state["chapters"][0]["body"] += "\n恢复副本继续写作。"
            status, _ = _request(base, "PUT", "/api/state", recovered_state, restored_project)
            _assert_status(status, {}, 200)
            source_after_restore = _request(base, "GET", "/api/state", project=manuscript_project)[1]
            assert "恢复副本继续写作。" not in source_after_restore["chapters"][0]["body"]
            with server.connect() as conn:
                assert moyu_backups.capture(conn, manuscript_project) == archive["payload"]

            # A damaged manifest digest must fail closed and keep both books
            # unchanged.
            corrupt = temp_root / "backups" / f"{backup_id}-corrupt.zip"
            corrupt.write_bytes(package.read_bytes())
            with zipfile.ZipFile(corrupt, "r") as source_zip:
                project_raw = source_zip.read("project.json")
                manifest = json.loads(source_zip.read("manifest.json"))
            manifest["sha256"] = "0" * 64
            with zipfile.ZipFile(corrupt, "w", zipfile.ZIP_DEFLATED) as target_zip:
                target_zip.writestr("project.json", project_raw)
                target_zip.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            package.unlink()
            corrupt.rename(package)
            before_corrupt_restore = _request(base, "GET", "/api/state", project=manuscript_project)[1]
            before_corrupt_projects = _request(base, "GET", "/api/projects")[1]["projects"]
            status, rejected = _request(
                base, "POST", f"/api/backups/{backup_id}/restore", {"confirm": True}, manuscript_project
            )
            assert status >= 400, rejected
            after_corrupt_restore = _request(base, "GET", "/api/state", project=manuscript_project)[1]
            assert after_corrupt_restore == before_corrupt_restore
            assert _request(base, "GET", "/api/projects")[1]["projects"] == before_corrupt_projects
            assert len(before_corrupt_projects) >= root_count

            # Completed imports must not exhaust the pending-preview limit.
            continuous_before = len(_request(base, "GET", "/api/projects")[1]["projects"])
            for number in range(5):
                continuous_text = f"第一章 连续导入\n第 {number + 1} 本新稿。\n"
                status, continuous_preview = _post_import_preview(
                    base, f"continuous-{number + 1}.txt", continuous_text.encode("utf-8")
                )
                _assert_status(status, continuous_preview, 200)
                status, continuous_book = _apply_import(
                    base,
                    continuous_preview,
                    mode="manuscript",
                    title=f"连续导入作品 {number + 1}",
                )
                _assert_status(status, continuous_book, 201)
                assert "".join(chapter["body"] for chapter in continuous_book["state"]["chapters"]) == continuous_text
            assert len(_request(base, "GET", "/api/projects")[1]["projects"]) == continuous_before + 5

            print("import/archive HTTP lifecycle fixtures: ok")
            return 0
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
