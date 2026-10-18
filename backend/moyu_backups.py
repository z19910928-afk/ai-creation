"""Verified, project-scoped complete archives and non-destructive recovery."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile
import zlib
from contextlib import closing
from pathlib import Path

from .moyu_records import decode_scope
from scripts.sqlite_backup import backup_database, verify_backup


SKIP_TABLES = {"migration_ledger", "app_backup_artifact", "app_search_document",
               "app_search_index_state", "app_search_fts", "index_checkpoint",
               "app_agent_connection"}
PLANNING_TABLES = {"app_planning_workflow", "app_planning_attempt",
                   "app_planning_event", "app_planning_application"}
AGENT_TABLES = {"app_agent_session", "app_agent_checkpoint", "app_agent_write"}
CHAT_TABLES = {"app_agent_chat_session", "app_agent_chat_turn", "app_agent_chat_action"}
PLANNING_RESTORE_REASON = "从备份恢复的规划仅供阅读，请重新开始规划；旧模型请求不会自动再次执行。"
PROJECT_TITLE_LIMIT = 200
RESTORE_TITLE_SUFFIX = " · 恢复副本"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 256_000_000
SCHEMA_VERSION = 11
KNOWN_ADDITIVE_TABLES = {"app_candidate_revision", "app_entity_scope"} | AGENT_TABLES | CHAT_TABLES
# The first complete v1 archive already included all of these tables.
REQUIRED_TABLES = set("""
project branch document_node document_revision event_log deletion_event source_span
entity candidate_memory ai_run merge_conflict audit_event actor assertion
character_state plot_thread external_source prov_entity prov_activity prov_agent
prov_edge export_job run_manifest app_project_meta app_state_version app_entity_meta
app_chapter_meta app_deleted_node app_plot_item app_ai_run_decision app_story_relation
app_timeline_event app_foreshadow_link app_chapter_commit app_ai_job app_ai_chunk
app_workspace app_blueprint app_chapter_structure app_candidate_application
app_model_settings app_quota_reservation app_assistant_request app_chapter_analysis
app_story_review app_manuscript_import app_reference_document app_reference_section
app_planning_workflow app_planning_attempt app_planning_event app_planning_application
""".split())
EXTRA_REFERENCES = {
    "branch": {"head_revision": "document_revision", "merge_base_revision": "document_revision"},
    "document_revision": {"actor_id": "actor"},
    "event_log": {"actor_id": "actor", "expected_head": "document_revision"},
    "deletion_event": {"actor_id": "actor"},
    "audit_event": {"actor_id": "actor"},
    "merge_conflict": {"merge_base": "document_revision", "left_revision": "document_revision",
                       "right_revision": "document_revision"},
    "character_state": {"event_id": "event_log"},
    "app_ai_job": {"retry_of": "ai_run"},
    "app_candidate_application": {"source_revision_id": "document_revision",
                                  "result_revision_id": "document_revision",
                                  "decision_id": "app_ai_run_decision"},
    "app_chapter_analysis": {"project_id": "project", "branch_id": "branch",
                            "chapter_id": "document_node", "source_revision_id": "document_revision",
                            "run_id": "ai_run"},
    "app_story_review": {"project_id": "project", "branch_id": "branch",
                        "chapter_id": "document_node", "source_revision_id": "document_revision",
                        "run_id": "ai_run"},
    "app_planning_workflow": {"project_id": "project", "branch_id": "branch"},
    "app_planning_attempt": {"job_id": "app_ai_job"},
    "app_planning_event": {"attempt_id": "app_planning_attempt"},
    "app_planning_application": {"project_id": "project", "branch_id": "branch"},
    "app_agent_session": {"project_id": "project", "branch_id": "branch",
                          "chapter_id": "document_node"},
    "app_agent_checkpoint": {"project_id": "project", "branch_id": "branch",
                             "session_id": "app_agent_session"},
    "app_agent_write": {"project_id": "project", "branch_id": "branch",
                        "session_id": "app_agent_session"},
    "app_agent_chat_session": {
        "project_id": "project", "branch_id": "branch",
    },
    "app_agent_chat_turn": {
        "project_id": "project", "branch_id": "branch",
        "session_id": "app_agent_chat_session", "chapter_id": "document_node",
        "job_id": "app_ai_job",
    },
    "app_agent_chat_action": {
        "project_id": "project", "branch_id": "branch",
        "session_id": "app_agent_chat_session", "turn_id": "app_agent_chat_turn",
        "chapter_id": "document_node",
    },
}
PROVENANCE_TYPES = {
    "entity": "entity", "document_node": "document_node", "revision": "document_revision",
    "source_span": "source_span", "assertion": "assertion", "character_state": "character_state",
    "event": "event_log", "ai_run": "ai_run", "actor": "actor",
    "external_source": "external_source", "prov_entity": "prov_entity",
    "prov_activity": "prov_activity", "prov_agent": "prov_agent",
}
ACTOR_TEXT_COLUMNS = {
    "candidate_memory": {"reviewed_by", "created_by"}, "assertion": {"reviewed_by"},
    "merge_conflict": {"resolver"}, "export_job": {"requested_by", "confirmed_by"},
}
HISTORICAL_ID_COLUMNS = {
    "event_log": {"aggregate_id"}, "deletion_event": {"aggregate_id"},
    "audit_event": {"target_id"},
}
JSON_REFERENCES = {
    "project_id": "project", "branch_id": "branch", "chapter_id": "document_node",
    "document_node_id": "document_node", "source_chapter_id": "document_node",
    "revision_id": "document_revision", "source_revision_id": "document_revision",
    "result_revision_id": "document_revision", "entity_id": "entity",
    "subject_entity_id": "entity", "from_entity_id": "entity", "to_entity_id": "entity",
    "source_span_id": "source_span", "candidate_memory_id": "candidate_memory",
    "assertion_id": "assertion", "source_assertion_id": "assertion",
    "character_state_id": "character_state", "plot_id": "app_plot_item",
    "memory_id": "candidate_memory", "relation_id": "app_story_relation",
    "timeline_id": "app_timeline_event", "foreshadow_link_id": "app_foreshadow_link",
    "event_id": "event_log", "ai_run_id": "ai_run", "run_id": "ai_run",
    "parent_run_id": "ai_run", "root_run_id": "ai_run", "retry_of": "ai_run",
    "job_id": "app_ai_job", "actor_id": "actor", "decision_id": "app_ai_run_decision",
    "workflow_id": "app_planning_workflow", "attempt_id": "app_planning_attempt",
    "source_document_id": "app_reference_document", "source_section_id": "app_reference_section",
    "chat_session_id": "app_agent_chat_session", "chat_turn_id": "app_agent_chat_turn",
    "chat_action_id": "app_agent_chat_action", "writing_session_id": "app_agent_session",
    "previous_chapter_id": "document_node", "next_chapter_id": "document_node",
}
JSON_REFERENCE_LISTS = {
    "entity_ids": "entity", "pov_entity_ids": "entity", "alias_matches": "entity",
    "known_by": "entity", "characters": "entity", "entities": "entity",
    "branches": "branch", "branch_ids": "branch",
}
JSON_TEXT_SUBTREES = {
    "request_audit", "payload", "messages", "text", "content", "body", "body_tail",
    "quote", "source_quote", "candidate_text", "instruction", "instructions", "author_notes",
    "notes", "title", "name", "summary", "reason", "note", "prompt", "sanitized_prompt",
}
JSON_OBJECT_IDENTITIES = {
    "project": "project",
    "current_chapter": "document_node", "chapter": "document_node",
    "chapters": "document_node", "previous_chapters": "document_node",
    "records": "entity", "memory": "candidate_memory",
    "commits": "app_chapter_commit",
    "planning_workflow": "app_planning_workflow",
}
JSON_KIND_IDENTITIES = {
    "chapter": "document_node", "entity": "entity", "memory": "candidate_memory",
    "reference": "app_reference_section", "reference_section": "app_reference_section",
    "reference_document": "app_reference_document", "assertion": "assertion",
    "source_span": "source_span", "character_state": "character_state",
    "timeline": "app_timeline_event", "relationship": "app_story_relation",
    "plot": "app_plot_item",
}
UNCHANGED_JSON = {
    ("entity", "aliases_json"), ("assertion", "object_json"),
    ("character_state", "value_json"),
    ("ai_run", "sampling_json"), ("ai_run", "tool_calls_json"),
    ("ai_run", "token_json"), ("ai_run", "cost_json"),
    ("app_manuscript_import", "metadata_json"), ("app_reference_document", "metadata_json"),
    ("app_reference_section", "source_json"),
}


def _strict_json(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("备份JSON包含重复字段")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("备份JSON含有非有限数值")

    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        value = json.loads(raw, object_pairs_hook=object_pairs, parse_constant=constant)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 128:
                raise ValueError("备份JSON层级超过支持范围")
            if isinstance(item, str):
                item.encode("utf-8", errors="strict")
            elif isinstance(item, dict):
                pending.extend((part, depth + 1) for pair in item.items() for part in pair)
            elif isinstance(item, list):
                pending.extend((part, depth + 1) for part in item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("备份JSON含有非有限数值")
        return value
    except (TypeError, UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("备份JSON损坏或文字编码无效") from error


def _payload_header(payload, *, full=True):
    if not isinstance(payload, dict):
        raise ValueError("备份项目资料需要为对象")
    if (payload.get("format") != "moyu-complete-project"
            or type(payload.get("version")) is not int or payload["version"] != 1):
        raise ValueError("不支持的备份格式")
    expected_version = SCHEMA_VERSION if full else 0
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != expected_version:
        raise ValueError("备份与当前版本的数据格式不一致")
    project = payload.get("project_id")
    data = payload.get("tables")
    if not isinstance(project, str) or not project or not isinstance(data, dict):
        raise ValueError("备份缺少完整资料")
    if full and (REQUIRED_TABLES - set(data) or set(data) - REQUIRED_TABLES - KNOWN_ADDITIVE_TABLES):
        raise ValueError("备份缺少必须的完整资料表或包含不支持的数据表")
    for name, rows in data.items():
        if not isinstance(name, str) or not isinstance(rows, list):
            raise ValueError("备份记录类型错误")
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("备份记录损坏")
    projects = data.get("project", [])
    if len(projects) != 1 or projects[0].get("id") != project:
        raise ValueError("备份作品身份不一致")
    name = projects[0].get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("备份中的作品名无效")
    if full and (type(projects[0].get("schema_version")) is not int
                 or projects[0]["schema_version"] != SCHEMA_VERSION):
        raise ValueError("备份作品结构版本不一致")
    for branch in data.get("branch", []):
        if (not isinstance(branch.get("id"), str) or not branch["id"]
                or branch.get("project_id") != project):
            raise ValueError("备份中的分支不属于当前作品")
    if not any(row.get("status") == "active" for row in data.get("branch", [])):
        raise ValueError("备份缺少可用稿件分支")
    return data


def _manifest_binding(payload, manifest):
    if not isinstance(manifest, dict):
        raise ValueError("备份清单需要为对象")
    data = payload["tables"]
    for key in ("id", "created_at", "sha256", "branch_id"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError("备份清单字段缺失或类型错误")
    if (manifest.get("project_id") != payload["project_id"]
            or manifest.get("scope") != "complete_project"
            or manifest.get("project_title") != data["project"][0]["name"]):
        raise ValueError("备份清单与作品资料不一致")
    branches = {row.get("id"): row for row in data.get("branch", [])}
    branch = branches.get(manifest.get("branch_id"))
    if not branch or branch.get("project_id") != payload["project_id"]:
        raise ValueError("备份清单的稿件分支不属于当前作品")
    counts = manifest.get("row_counts")
    if (not isinstance(counts, dict) or any(type(value) is not int or value < 0 for value in counts.values())
            or counts != {table: len(rows) for table, rows in data.items()}):
        raise ValueError("备份记录数量校验失败")


def install_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS app_backup_artifact(
        id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES project(id),
        branch_id TEXT NOT NULL REFERENCES branch(id),name TEXT NOT NULL,
        created_at TEXT NOT NULL,size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,manifest_json TEXT NOT NULL)""")


def tables(conn):
    return [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")
        if row[0] not in SKIP_TABLES and not row[0].startswith("app_search_fts_")]


def _portable_model_settings(raw):
    from .moyu_settings import PARAMETERS, TASKS, _connection_options

    settings = _strict_json(raw)
    if not isinstance(settings, dict):
        raise ValueError("备份中的模型设置格式无效")
    public = {"base_url", "model", "models", "styles", "parameters", "task_parameters", "daily_limit",
              "api_protocol", "output_token_parameter", "include_usage",
              "provider", "custom_request_path", "models_path", "user_agent",
              "connections", "connection_workflow", "default_connection_id", "task_connections"}
    settings = {key: value for key, value in settings.items() if key in public}
    settings.update(_connection_options(settings))
    for key in ("models", "styles", "parameters", "task_parameters"):
        if key in settings and not isinstance(settings[key], dict):
            raise ValueError("备份中的模型设置格式无效")
    if "styles" in settings:
        settings["styles"] = {key: value for key, value in settings["styles"].items()
                              if key in {"tone", "instructions"}}
    def public_parameters(values):
        result = {key: value for key, value in values.items() if key in PARAMETERS}
        if isinstance(result.get("thinking"), dict):
            result["thinking"] = {key: value for key, value in result["thinking"].items()
                                  if key == "type"}
        return result

    if "parameters" in settings:
        settings["parameters"] = public_parameters(settings["parameters"])
    if "task_parameters" in settings:
        from .moyu_provider import prepare_chat_request
        task_parameters = {}
        for task, values in settings["task_parameters"].items():
            if task not in TASKS:
                continue
            if not isinstance(values, dict):
                raise ValueError("备份中的用途参数格式无效")
            clean = public_parameters(values)
            prepare_chat_request(
                {"base_url": "http://127.0.0.1", "model": "backup-validation",
                 "parameters": {key: value for key, value in clean.items() if key != "max_tokens"}},
                [{"role": "user", "content": "backup-validation"}],
                task=task, max_tokens=clean.get("max_tokens", 3000))
            if clean:
                task_parameters[task] = clean
        settings["task_parameters"] = task_parameters
    if "models" in settings:
        settings["models"] = {key: value for key, value in settings["models"].items() if key in TASKS}
    if "connections" in settings:
        if not isinstance(settings["connections"], list) or len(settings["connections"]) > 100:
            raise ValueError("备份中的模型库格式无效")
        connections = []
        identifiers = set()
        for connection in settings["connections"]:
            if not isinstance(connection, dict):
                raise ValueError("备份中的模型连接格式无效")
            clean = {}
            for key, limit in (("id", 100), ("name", 200), ("base_url", 2048), ("model", 200)):
                value = connection.get(key, "")
                if not isinstance(value, str) or len(value) > limit:
                    raise ValueError("备份中的模型连接格式无效")
                clean[key] = value
            if not clean["id"] or clean["id"] in identifiers:
                raise ValueError("备份中的模型连接身份无效")
            identifiers.add(clean["id"])
            clean.update(_connection_options(connection))
            clean.update(_credential_ref=None, last_probe=None, revision="restored")
            connections.append(clean)
        settings["connections"] = connections
    if "connection_workflow" in settings and type(settings["connection_workflow"]) is not bool:
        raise ValueError("备份中的模型设置流程无效")
    if "default_connection_id" in settings:
        if not isinstance(settings["default_connection_id"], str):
            raise ValueError("备份中的默认模型无效")
        settings["default_connection_id"] = ""
    if "task_connections" in settings:
        if not isinstance(settings["task_connections"], dict):
            raise ValueError("备份中的用途模型格式无效")
        settings["task_connections"] = {}
    if settings.get("connection_workflow"):
        settings.update(base_url="", model="", models={})
    settings["_credential_ref"] = None
    settings["last_probe"] = None
    return json.dumps(settings, ensure_ascii=False, allow_nan=False)


def capture(conn, project):
    result = {}
    table_names = tables(conn)
    for table in table_names:
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if table == "project":
            query, args = 'SELECT * FROM project WHERE id=?', (project,)
        elif "project_id" in columns:
            query, args = f'SELECT * FROM "{table}" WHERE project_id=?', (project,)
        elif "workflow_id" in columns and "app_planning_workflow" in table_names:
            query, args = (
                f'SELECT * FROM "{table}" WHERE workflow_id IN '
                '(SELECT id FROM app_planning_workflow WHERE project_id=?)',
                (project,),
            )
        elif "document_node_id" in columns:
            query, args = f'SELECT * FROM "{table}" WHERE document_node_id IN (SELECT id FROM document_node WHERE project_id=?)', (project,)
        elif "chapter_id" in columns:
            query, args = f'SELECT * FROM "{table}" WHERE chapter_id IN (SELECT id FROM document_node WHERE project_id=?)', (project,)
        elif "entity_id" in columns:
            query, args = f'SELECT * FROM "{table}" WHERE entity_id IN (SELECT id FROM entity WHERE project_id=?)', (project,)
        else:
            continue
        rows = []
        for row in conn.execute(query, args):
            item = dict(row)
            if table == "app_model_settings":
                item["settings_json"] = _portable_model_settings(item["settings_json"])
            for key, value in item.items():
                if isinstance(value, bytes):
                    item[key] = {"$blob": base64.b64encode(value).decode("ascii")}
            rows.append(item)
        result[table] = rows
    # Global author/provenance agents are included only when this book actually
    # references them. Recovery must never borrow their IDs from another device.
    for table in ("actor", "prov_agent"):
        if table not in result:
            continue
        referenced = set()
        for owner, rows in result.items():
            for row in rows:
                if table == "actor":
                    referenced.update(row.get(field) for field in
                                      ({"actor_id"} | ACTOR_TEXT_COLUMNS.get(owner, set())))
                if owner == "prov_entity" and row.get("entity_type") == table:
                    referenced.add(row["entity_ref"])
                if owner == "prov_edge":
                    referenced.update((row["subject_id"], row["object_id"]))
        for row in conn.execute(f'SELECT * FROM "{table}" WHERE project_id IS NULL ORDER BY rowid'):
            if row["id"] in referenced:
                result[table].append(dict(row))
    return {"format": "moyu-complete-project", "version": 1, "project_id": project,
            "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "tables": result}


def create(conn, database, directory, project, branch, timestamp):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Use the established online backup and integrity checks to obtain one
    # consistent revision before extracting just the requested manuscript.
    with tempfile.TemporaryDirectory(prefix="moyu-backup-") as tmp:
        snapshot = Path(tmp) / "snapshot.sqlite"
        backup_database(database, snapshot)
        verification = verify_backup(snapshot)
        with closing(sqlite3.connect(snapshot)) as source:
            source.row_factory = sqlite3.Row
            payload = capture(source, project)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    backup_id = "backup_" + uuid.uuid4().hex
    title = conn.execute("SELECT name FROM project WHERE id=?", (project,)).fetchone()[0]
    manifest = {"id": backup_id, "project_id": project, "branch_id": branch,
                "project_title": title, "scope": "complete_project",
                "created_at": timestamp, "sha256": digest, "verified": True,
                "row_counts": {key: len(value) for key, value in payload["tables"].items()},
                "source_integrity": verification.get("integrity_check", "ok"),
                "excluded": ["credentials", "other_projects", "rebuildable_search_index"]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", raw)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    package = buffer.getvalue()
    path = directory / (backup_id + ".zip")
    with path.open("xb") as output:
        output.write(package)
    loaded = read(path, project)
    if loaded["sha256"] != digest:
        raise ValueError("backup verification failed")
    conn.execute("INSERT INTO app_backup_artifact VALUES(?,?,?,?,?,?,?,?)",
                 (backup_id, project, branch, title, timestamp, len(package), digest,
                  json.dumps(manifest, ensure_ascii=False)))
    return {**manifest, "name": title, "size_bytes": len(package)}


def read_bytes(raw, project=None):
    """Read bounded original ZIP bytes without extracting files or writing data."""
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("请选择非空的完整备份ZIP")
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("完整备份ZIP超过支持大小")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) != 2 or {item.filename for item in infos} != {"project.json", "manifest.json"}:
                raise ValueError("备份内容不完整或含重复文件")
            if sum(item.file_size for item in infos) > MAX_EXPANDED_BYTES:
                raise ValueError("备份展开后超过支持的大小")
            for item in infos:
                kind = stat.S_IFMT(item.external_attr >> 16)
                if (item.is_dir() or kind not in {0, stat.S_IFREG}
                        or item.flag_bits & 1
                        or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                    raise ValueError("备份含有不支持的内部文件")
            project_raw = archive.read("project.json")
            manifest = _strict_json(archive.read("manifest.json"))
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError, zlib.error) as error:
        raise ValueError("完整备份ZIP损坏，无法读取") from error
    if not isinstance(manifest, dict):
        raise ValueError("备份清单需要为对象")
    digest = hashlib.sha256(project_raw).hexdigest()
    if digest != manifest.get("sha256"):
        raise ValueError("备份校验失败，原作品未改动")
    payload = _strict_json(project_raw)
    _payload_header(payload)
    _manifest_binding(payload, manifest)
    if project is not None and payload["project_id"] != project:
        raise ValueError("这份备份属于其他作品")
    return {"payload": payload, "manifest": manifest, "sha256": digest,
            "archive_sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


def read(path, project=None):
    if hasattr(path, "read"):
        raw = path.read(MAX_ARCHIVE_BYTES + 1)
    else:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_ARCHIVE_BYTES + 1)
    return read_bytes(raw, project)


def _blob(value):
    if not isinstance(value, dict) or set(value) != {"$blob"} or not isinstance(value["$blob"], str):
        raise ValueError("备份中的原始字节格式无效")
    try:
        return base64.b64decode(value["$blob"], validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("备份中的原始字节校验失败") from error


def _schema(conn, data):
    return {
        table: {
            "columns": list(conn.execute(f'PRAGMA table_info("{table}")')),
            "foreign_keys": list(conn.execute(f'PRAGMA foreign_key_list("{table}")')),
        }
        for table in data
    }


def _decoded_data(data, schema):
    decoded = copy.deepcopy(data)
    identifiers = {}
    for table, rows in decoded.items():
        columns = schema[table]["columns"]
        column_names = {column[1] for column in columns}
        primary = [column[1] for column in columns if column[5]]
        seen = set()
        for row in rows:
            if set(row) != column_names:
                raise ValueError("备份与当前版本的数据格式不一致")
            for column in columns:
                _, name, kind, required, _, is_primary = tuple(column)
                value = row[name]
                if value is None:
                    if required or is_primary:
                        raise ValueError("备份记录缺少必要字段")
                    continue
                if kind.upper() == "BLOB":
                    row[name] = _blob(value)
                elif kind.upper() == "TEXT":
                    if not isinstance(value, str):
                        raise ValueError("备份文字字段类型错误")
                    try:
                        value.encode("utf-8", errors="strict")
                    except UnicodeError as error:
                        raise ValueError("备份文字编码无效") from error
                elif kind.upper() == "INTEGER":
                    if type(value) is not int or not -(2**63) <= value < 2**63:
                        raise ValueError("备份整数字段类型错误")
                elif kind.upper() == "REAL":
                    if type(value) not in {int, float} or not math.isfinite(value):
                        raise ValueError("备份数值字段类型错误")
                elif not isinstance(value, (str, int, float, bytes)):
                    raise ValueError("备份字段类型错误")
                if name.endswith("_json") and value is not None:
                    _strict_json(value)
            identity = tuple(row[key] for key in primary)
            if not primary or identity in seen or any(value == "" for value in identity):
                raise ValueError("备份记录身份重复或缺失")
            seen.add(identity)
            if "id" in row:
                identifier = row["id"]
                if identifier in identifiers:
                    raise ValueError("备份的持久身份重复")
                identifiers[identifier] = (table, row)
    return decoded, identifiers


def _check_graph(data, schema, project, *, full):
    indexes = {table: {row["id"]: row for row in rows if "id" in row}
               for table, rows in data.items()}
    all_ids = {identity: row for index in indexes.values() for identity, row in index.items()}

    def require(table, identity):
        if identity is None:
            return None
        result = indexes.get(table, {}).get(identity)
        if result is None:
            raise ValueError("备份中的资料引用不属于当前作品或缺少原记录")
        return result

    def branch_of(table, row):
        if row.get("branch_id") is not None:
            return row["branch_id"]
        if table == "branch":
            return row["id"]
        if row.get("chapter_id") is not None:
            return require("document_node", row["chapter_id"]).get("branch_id")
        if row.get("document_node_id") is not None:
            return require("document_node", row["document_node_id"]).get("branch_id")
        if row.get("workflow_id") is not None:
            return require("app_planning_workflow", row["workflow_id"]).get("branch_id")
        return None

    for table, rows in data.items():
        for row in rows:
            if "project_id" in row and row["project_id"] != project:
                if not (table in {"actor", "prov_agent"} and row["project_id"] is None):
                    raise ValueError("备份包含其他作品资料")
            if row.get("branch_id") is not None:
                branch = require("branch", row["branch_id"])
                if branch.get("project_id") != project:
                    raise ValueError("备份中的分支不属于当前作品")
            references = {fk[3]: (fk[2], fk[4]) for fk in schema[table]["foreign_keys"]}
            references.update({field: (target, "id")
                               for field, target in EXTRA_REFERENCES.get(table, {}).items()
                               if field in row})
            for field, (target_table, target_column) in references.items():
                identity = row[field]
                if identity is None:
                    continue
                # The established schema-0 planning unit fixture has no job table.
                if not full and table == "app_planning_attempt" and target_table not in data:
                    continue
                if target_column == "id":
                    related = require(target_table, identity)
                else:
                    related = next((item for item in data.get(target_table, [])
                                    if item.get(target_column) == identity), None)
                    if related is None:
                        raise ValueError("备份中的资料引用缺失")
                child_branch = branch_of(table, row)
                related_branch = branch_of(target_table, related)
                if (child_branch is not None and related_branch is not None
                        and table not in {"branch", "merge_conflict"}
                        and not (table == "event_log" and field == "expected_head")
                        and child_branch != related_branch):
                    raise ValueError("备份中的资料跨越了稿件分支")
            if table == "document_revision":
                node = require("document_node", row["document_node_id"])
                parent = require("document_revision", row.get("parent_id"))
                if node["branch_id"] != row["branch_id"] or (
                        parent and parent["document_node_id"] != row["document_node_id"]):
                    raise ValueError("备份中的正文版本归属不一致")
            elif table == "branch" and row.get("head_revision") is not None:
                if require("document_revision", row["head_revision"])["branch_id"] != row["id"]:
                    raise ValueError("备份中的分支稿件版本不一致")
            elif table == "source_span":
                if require("document_revision", row["revision_id"])["document_node_id"] != row["document_node_id"]:
                    raise ValueError("备份中的原句与章节版本不一致")
            elif table in {"app_chapter_commit", "app_candidate_application",
                           "app_chapter_analysis", "app_story_review"}:
                for field in ("revision_id", "source_revision_id", "result_revision_id"):
                    if row.get(field) and require("document_revision", row[field])["document_node_id"] != row["chapter_id"]:
                        raise ValueError("备份中的章节记录与正文版本不一致")
                if table == "app_candidate_application":
                    decision = require("app_ai_run_decision", row["decision_id"])
                    if decision["ai_run_id"] != row["ai_run_id"]:
                        raise ValueError("备份中的采纳决定与模型记录不一致")
            elif table == "character_state" and row.get("event_id"):
                require("event_log", row["event_id"])
            elif table == "prov_entity":
                target = PROVENANCE_TYPES.get(row["entity_type"])
                if target is None:
                    raise ValueError("备份中的来源类型不受支持")
                require(target, row["entity_ref"])
            elif table == "prov_edge":
                if row["subject_id"] not in all_ids or row["object_id"] not in all_ids:
                    raise ValueError("备份中的来源引用缺失")
            elif table == "app_planning_event" and row.get("attempt_id"):
                if require("app_planning_attempt", row["attempt_id"])["workflow_id"] != row["workflow_id"]:
                    raise ValueError("备份中的规划记录不属于当前作品")
            elif table == "app_planning_application":
                workflow = require("app_planning_workflow", row["workflow_id"])
                if workflow["project_id"] != project or workflow["branch_id"] != row["branch_id"]:
                    raise ValueError("备份中的规划应用与原规划不一致")
            elif table == "app_ai_job":
                run = require("ai_run", row["ai_run_id"])
                if run["branch_id"] != row["branch_id"]:
                    raise ValueError("备份中的任务与模型记录不一致")
                context = _strict_json(row["context_json"])
                if not isinstance(context, dict):
                    raise ValueError("备份中的模型任务上下文格式无效")
                if row.get("result_json") is not None and not isinstance(_strict_json(row["result_json"]), dict):
                    raise ValueError("备份中的模型结果格式无效")
                current = context.get("current_chapter")
                if current is not None and (
                        not isinstance(current, dict) or current.get("id") != row["chapter_id"]):
                    raise ValueError("备份中的模型任务与当前章节不一致")
                source = context.get("_source")
                if isinstance(source, dict) and source.get("chapter_id") not in (None, row["chapter_id"]):
                    raise ValueError("备份中的模型任务与来源章节不一致")
                for binding_key in ("_candidate_revision", "candidate_revision"):
                    if context.get(binding_key) and "app_candidate_revision" not in data:
                        raise ValueError("备份缺少候选整理关系")
            elif table == "ai_run":
                if not isinstance(_strict_json(row["consent_json"]), dict):
                    raise ValueError("备份中的模型授权记录格式无效")
                consent = _strict_json(row["consent_json"])
                if consent.get("candidate_revision") and "app_candidate_revision" not in data:
                    raise ValueError("备份缺少候选整理关系")

    for row in data.get("app_candidate_revision", []):
        parent = require("ai_run", row["parent_run_id"])
        child = require("app_ai_job", row["job_id"])
        binding = _strict_json(child["context_json"]).get("_candidate_revision")
        if (parent["branch_id"] != row["branch_id"] or child["branch_id"] != row["branch_id"]
                or not isinstance(binding, dict) or binding.get("parent_run_id") != row["parent_run_id"]
                or binding.get("parent_hash") != row["candidate_hash"]
                or binding.get("request_id") != row["request_id"]
                or binding.get("request_hash") != row["request_hash"]
                or not isinstance(binding.get("root_run_id"), str)):
            raise ValueError("备份中的父子候选关系不一致")
        require("ai_run", binding.get("root_run_id"))
    for row in data.get("app_planning_attempt", []):
        if row.get("job_id") and full:
            job = require("app_ai_job", row["job_id"])
            workflow = require("app_planning_workflow", row["workflow_id"])
            declaration = _strict_json(job["context_json"]).get("planning_workflow")
            if (job["project_id"] != project or job["branch_id"] != workflow["branch_id"]
                    or not isinstance(declaration, dict)
                    or declaration.get("id") != row["workflow_id"]
                    or declaration.get("attempt_id") != row["id"]
                    or declaration.get("stage") != row["stage"]):
                raise ValueError("备份中的规划任务与原规划不一致")
    agent_checkpoints = {
        (row["session_id"], row["checkpoint_ns"], row["checkpoint_key"]): row
        for row in data.get("app_agent_checkpoint", [])
    }
    for row in data.get("app_agent_checkpoint", []):
        if row["parent_key"] is not None and (
                row["session_id"], row["checkpoint_ns"], row["parent_key"]) not in agent_checkpoints:
            raise ValueError("备份中的写作任务检查点缺少同一会话的前序记录")
    for row in data.get("app_agent_write", []):
        if (row["session_id"], row["checkpoint_ns"], row["checkpoint_key"]) not in agent_checkpoints:
            raise ValueError("备份中的写作任务写入不属于同一会话的检查点")
    for row in data.get("app_timeline_event", []):
        declared = _strict_json(row["entity_ids_json"])
        if (not isinstance(declared, list)
                or any(not isinstance(identity, str) or identity not in indexes.get("entity", {})
                       for identity in declared)):
            raise ValueError("备份中的时间线人物引用不属于当前作品")
    for table, rows in data.items():
        if table in PLANNING_TABLES or table in AGENT_TABLES:
            continue
        for row in rows:
            for field, raw in row.items():
                if field.endswith("_json") and raw is not None and (table, field) not in UNCHANGED_JSON:
                    if table == "event_log" and row["event_type"] == "project.portable_restored":
                        continue
                    _check_json_graph(_strict_json(raw), indexes, project,
                                      branch_of(table, row), historical=table in {
                                          "event_log", "deletion_event", "audit_event",
                                      }, names=table == "app_entity_scope")
    _check_chat_graph(data, indexes, project, branch_of)
    return indexes


def _check_chat_graph(data, indexes, project, branch_of):
    """Validate chat ownership without interpreting message prose as IDs."""
    for table in CHAT_TABLES:
        for row in data.get(table, []):
            session = (row if table == "app_agent_chat_session" else
                       indexes.get("app_agent_chat_session", {}).get(row["session_id"]))
            row_branch = branch_of(table, row)
            if session is None or session["project_id"] != project or session["branch_id"] != row_branch:
                raise ValueError("备份中的聊天记录跨越了稿件分支")

            turn_id = row.get("turn_id")
            if turn_id is not None:
                turn = indexes.get("app_agent_chat_turn", {}).get(turn_id)
                if turn is None or turn["session_id"] != session["id"]:
                    raise ValueError("备份中的聊天消息不属于当前会话")
            if table == "app_agent_chat_turn":
                if row["status"] not in {"queued", "running", "success", "failed", "canceled"}:
                    raise ValueError("备份中的聊天回复状态无效")
                if row["job_id"]:
                    context = _strict_json(indexes["app_ai_job"][row["job_id"]]["context_json"])
                    binding = context.get("_agent_chat")
                    if (not isinstance(binding, dict)
                            or binding.get("chat_session_id") != row["session_id"]
                            or binding.get("chat_turn_id") != row["id"]):
                        raise ValueError("备份中的聊天消息与模型任务归属不一致")
            if table == "app_agent_chat_action":
                if (row["kind"] not in {"continue", "polish", "inspect", "plan", "next_chapter", "open"}
                        or row["status"] not in {"proposed", "confirming", "confirmed", "dismissed"}):
                    raise ValueError("备份中的聊天动作类型或状态无效")

            for field, raw in row.items():
                if field.endswith("_json") and raw is not None:
                    parsed = _strict_json(raw)
                    expected = list if field == "references_json" else dict
                    if not isinstance(parsed, expected) and not (field == "source_json" and parsed is None):
                        raise ValueError("备份中的聊天资料格式无效")


def _check_json_graph(value, indexes, project, branch, *, key="", historical=False, names=False):
    if key in {"request_audit", "payload", "messages", "structured"}:
        return
    if key in {"knowledge_scope", "known_by"}:
        names = True
    if key in JSON_REFERENCES and value not in (None, ""):
        target = JSON_REFERENCES[key]
        if not isinstance(value, str):
            raise ValueError("备份中的资料身份字段类型无效")
        related = indexes.get(target, {}).get(value)
        if related is None:
            if historical or names:
                return
            raise ValueError("备份中的JSON资料引用缺失或不属于当前作品")
        if target == "project" and value != project and not historical:
            raise ValueError("备份中的JSON作品身份不一致")
        related_branch = related["id"] if target == "branch" else related.get("branch_id")
        if branch and related_branch and related_branch != branch and not historical:
            raise ValueError("备份中的JSON资料跨越了稿件分支")
        return
    if key == "reference_passages":
        if not isinstance(value, list) or len(value) > 1:
            raise ValueError("备份中的本次参考片段格式无效")
        for passage in value:
            if not isinstance(passage, dict):
                raise ValueError("备份中的本次参考片段格式无效")
            if any(not isinstance(passage.get(field), str) for field in ("document_id", "section_id")):
                raise ValueError("备份中的本次参考来源身份格式无效")
            document = indexes.get("app_reference_document", {}).get(passage.get("document_id"))
            section = indexes.get("app_reference_section", {}).get(passage.get("section_id"))
            if (not document or not section or section["document_id"] != document["id"]
                    or passage.get("project_id") != project or passage.get("branch_id") != branch
                    or section["branch_id"] != branch or document["branch_id"] != branch):
                raise ValueError("备份中的本次参考来源不属于当前作品和分支")
            start, end = passage.get("section_start"), passage.get("section_end")
            source = _strict_json(section["source_json"])
            if (not isinstance(source, dict) or type(source.get("source_start")) is not int
                    or type(source.get("source_end")) is not int
                    or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(section["body"])
                    or type(passage.get("source_start")) is not int or type(passage.get("source_end")) is not int
                    or passage.get("source_quote") != section["body"][start:end]
                    or passage.get("source_sha256") != document["source_sha256"]
                    or passage.get("section_hash") != hashlib.sha256(section["body"].encode("utf-8")).hexdigest()
                    or passage.get("source_hash") != hashlib.sha256(section["body"][start:end].encode("utf-8")).hexdigest()
                    or passage.get("source_start") != source["source_start"] + start
                    or passage.get("source_end") != source["source_start"] + end
                    or passage.get("id") != section["id"] + ":" + str(start) + ":" + str(end)):
                raise ValueError("备份中的本次参考原句范围或指纹不一致")
        return
    if isinstance(value, list):
        if key in JSON_REFERENCE_LISTS:
            if any(not isinstance(item, str) for item in value):
                raise ValueError("备份中的资料身份列表格式无效")
            # Character and branch names are permitted by the saved scope format.
            if key not in {"known_by", "characters", "entities", "branches", "branch_ids"}:
                target = JSON_REFERENCE_LISTS[key]
                if any(item not in indexes.get(target, {}) for item in value):
                    raise ValueError("备份中的资料身份列表引用缺失")
            return
        for item in value:
            _check_json_graph(item, indexes, project, branch, key=key, historical=historical, names=names)
    elif isinstance(value, dict):
        kind = value.get("kind") or value.get("material_kind")
        target = JSON_OBJECT_IDENTITIES.get(key, JSON_KIND_IDENTITIES.get(kind) if isinstance(kind, str) else None)
        if target and key in {"current_chapter", "planning_workflow", "dependencies", "commits"}:
            identity = value.get("id")
            related = indexes.get(target, {}).get(identity) if isinstance(identity, str) else None
            if related is None and not historical:
                raise ValueError("备份中的JSON对象身份不属于当前作品")
            if related and branch and related.get("branch_id") not in (None, branch) and not historical:
                raise ValueError("备份中的JSON对象跨越了稿件分支")
        source_revision = value.get("source_revision_id") or value.get("revision_id")
        source_chapter = value.get("source_chapter_id") or value.get("chapter_id")
        if (source_revision and source_chapter and not historical
                and isinstance(source_revision, str) and isinstance(source_chapter, str)):
            revision = indexes.get("document_revision", {}).get(source_revision)
            if revision and revision["document_node_id"] != source_chapter:
                raise ValueError("备份中的JSON来源章节与正文版本不一致")
        if ("source_span_id" in value and isinstance(value["source_span_id"], str)
                and not historical and value["source_span_id"] in indexes.get("source_span", {})):
            span = indexes["source_span"][value["source_span_id"]]
            if ((source_revision and span["revision_id"] != source_revision)
                    or (source_chapter and span["document_node_id"] != source_chapter)):
                raise ValueError("备份中的JSON原句与正文来源不一致")
        for name, item in value.items():
            _check_json_graph(item, indexes, project, branch, key=name, historical=historical, names=names)


def _check_content(data, indexes):
    revision_text = {}
    for row in data.get("document_revision", []):
        try:
            text = row["canonical_text"].decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ValueError("备份正文的原始文字编码无效") from error
        if hashlib.sha256(row["canonical_text"]).hexdigest() != row["content_hash"]:
            raise ValueError("备份正文内容指纹不一致")
        revision_text[row["id"]] = text
    for row in data.get("source_span", []):
        text = revision_text[row["revision_id"]]
        start, end = row["start_codepoint"], row["end_codepoint"]
        if (not 0 <= start <= end <= len(text) or text[start:end] != row["quote"]
                or hashlib.sha256(row["quote"].encode("utf-8")).hexdigest() != row["quote_hash"]
                or len(text[:start].encode("utf-8")) != row["start_utf8"]
                or len(text[:end].encode("utf-8")) != row["end_utf8"]):
            raise ValueError("备份中的原句范围或内容指纹不一致")
    for table in ("app_chapter_analysis", "app_story_review"):
        for row in data.get(table, []):
            revision = indexes["document_revision"][row["source_revision_id"]]
            if row["chapter_hash"] != revision["content_hash"]:
                raise ValueError("备份中的历史报告与原稿指纹不一致")
    for table in ("app_manuscript_import", "app_reference_document"):
        for row in data.get(table, []):
            if hashlib.sha256(row["source_bytes"]).hexdigest() != row["source_sha256"]:
                raise ValueError("备份中的原始来源文件指纹不一致")
            from .moyu_import import parse_import
            parsed = parse_import(row["source_bytes"], row["filename"])
            metadata = _strict_json(row["metadata_json"])
            text = "".join(chapter["body"] for chapter in parsed["chapters"])
            if (not isinstance(metadata, dict) or parsed["metadata"]["text_sha256"] != row["text_sha256"]
                    or metadata.get("text_sha256") != row["text_sha256"]):
                raise ValueError("备份中的原始来源文字指纹不一致")
            if table == "app_reference_document":
                sections = sorted((item for item in data.get("app_reference_section", [])
                                   if item["document_id"] == row["id"]), key=lambda item: item["ordinal"])
                if (not sections or [section["ordinal"] for section in sections] != list(range(len(sections)))
                        or "".join(section["body"] for section in sections) != text):
                    raise ValueError("备份中的参考原文有缺失、重复或改动")
                cursor = 0
                for section in sections:
                    source = _strict_json(section["source_json"])
                    end = cursor + len(section["body"])
                    if (not isinstance(source, dict) or type(source.get("source_start")) is not int
                            or type(source.get("source_end")) is not int
                            or source["source_start"] != cursor or source["source_end"] != end
                            or source.get("source_hash") != hashlib.sha256(section["body"].encode("utf-8")).hexdigest()):
                        raise ValueError("备份中的参考原句范围或指纹不一致")
                    origins = source.get("sources")
                    expected_origins = []
                    for chapter in parsed["chapters"]:
                        left, right = max(cursor, chapter["source_start"]), min(end, chapter["source_end"])
                        if left < right:
                            expected_origins.append({
                                "preview_chapter_id": chapter["id"], "source_start": left, "source_end": right,
                                **{key: chapter[key] for key in
                                   ("source_member", "source_member_hash", "source_member_text_hash")
                                   if key in chapter},
                            })
                    if origins != expected_origins:
                        raise ValueError("备份中的参考章节原文件来源不一致")
                    cursor = end
    for row in data.get("app_entity_scope", []):
        decode_scope(row["scope_json"])
    for row in data.get("app_agent_session", []):
        plan = _strict_json(row["plan_json"])
        if (not isinstance(plan, dict) or not isinstance(plan.get("summary", {}), dict)
                or not isinstance(plan.get("steps", {}), dict)
                or any(not isinstance(step, dict) for step in plan.get("steps", {}).values())):
            raise ValueError("备份中的写作任务历史摘要格式无效")
        if ("mode" in plan and (not isinstance(plan["mode"], str)
                                or plan["mode"] not in {"continue", "polish", "inspect", "plan"})):
            raise ValueError("备份中的写作任务历史摘要格式无效")
        summary = plan.get("summary", {})
        if any(field in summary and not isinstance(summary[field], str) for field in
               ("stage", "status", "error", "current_job_id", "current_ai_run_id")):
            raise ValueError("备份中的写作任务历史摘要格式无效")
        for field, minimum in (("calls_used", 0), ("max_calls", 1)):
            if field in summary and (type(summary[field]) is not int
                                     or not minimum <= summary[field] <= 4):
                raise ValueError("备份中的写作任务历史摘要格式无效")
    for row in data.get("app_model_settings", []):
        _portable_model_settings(row["settings_json"])
    for row in data.get("app_ai_job", []):
        run = indexes["ai_run"][row["ai_run_id"]]
        if row["result_json"] is not None:
            result = _strict_json(row["result_json"])
            if (run["output_hash"] is not None and isinstance(result.get("text"), str)
                    and hashlib.sha256(result["text"].encode("utf-8")).hexdigest() != run["output_hash"]):
                raise ValueError("备份中的模型原始结果指纹不一致")
            if result.get("response_format") == "json" and isinstance(result.get("text"), str):
                structured = result.get("structured")
                if isinstance(structured, dict) and isinstance(structured.get("content"), str):
                    transport = _strict_json(result["text"])
                    if not isinstance(transport, dict) or transport.get("content") != structured["content"]:
                        raise ValueError("备份中的JSON正文与模型原始结果不一致")
    for run_id in {row["ai_run_id"] for row in data.get("app_ai_chunk", [])}:
        chunks = sorted((row for row in data["app_ai_chunk"] if row["ai_run_id"] == run_id),
                        key=lambda row: row["seq"])
        parts = []
        for index, chunk in enumerate(chunks, 1):
            parts.append(chunk["text"])
            if (chunk["seq"] != index
                    or hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest() != chunk["payload_hash"]
                    or hashlib.sha256("".join(parts).encode("utf-8")).hexdigest() != chunk["cumulative_hash"]):
                raise ValueError("备份中的模型分片顺序或内容指纹不一致")
    if data.get("app_planning_workflow"):
        from .moyu_planning import _hash
        for row in data["app_planning_workflow"]:
            if _hash({"version": row["source_version"],
                      "blueprint": _strict_json(row["source_blueprint_json"])}) != row["source_hash"]:
                raise ValueError("备份中的规划原始来源指纹不一致")
        for row in data.get("app_planning_attempt", []):
            if row["candidate_json"] is not None and _hash(_strict_json(row["candidate_json"])) != row["candidate_hash"]:
                raise ValueError("备份中的规划候选指纹不一致")


def _table_order(data, schema):
    dependencies = {}
    for table in data:
        related = {fk[2] for fk in schema[table]["foreign_keys"]}
        related.update(EXTRA_REFERENCES.get(table, {}).values())
        if table == "branch":
            related.discard("document_revision")
        if table == "prov_entity":
            related.update(PROVENANCE_TYPES.get(row["entity_type"]) for row in data[table])
        if table == "prov_edge":
            related.update(PROVENANCE_TYPES.values())
        dependencies[table] = (related & set(data)) - {table}
    remaining, done, result = set(data), set(), []
    while remaining:
        ready = [table for table in data if table in remaining and dependencies[table] <= done]
        if not ready:
            raise ValueError("备份的资料依赖无法恢复")
        result.extend(ready)
        done.update(ready)
        remaining.difference_update(ready)
    return result


def _insert_data(conn, data, order):
    for table in order:
        for row in data[table]:
            keys = list(row)
            placeholders = ",".join("?" for _ in keys)
            column_sql = ",".join(f'"{key}"' for key in keys)
            values = [row[key] for key in keys]
            if table == "export_job" and row.get("status") == "success":
                values[keys.index("status")] = "awaiting_confirmation"
            conn.execute(f'INSERT INTO "{table}" ({column_sql}) VALUES({placeholders})', values)
            if table == "export_job" and row.get("status") == "success":
                conn.execute("UPDATE export_job SET status=?,confirmed_by=? WHERE id=?",
                             ("success", row["confirmed_by"], row["id"]))


def validate_payload(conn, payload, *, manifest=None):
    """Validate a closed project graph against schema, with no target writes."""
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    full = current_version != 0
    if full and current_version != SCHEMA_VERSION:
        raise ValueError("当前版本不支持这份完整备份")
    data = _payload_header(payload, full=full)
    allowed = set(tables(conn))
    required = REQUIRED_TABLES if full else allowed - KNOWN_ADDITIVE_TABLES
    if set(data) - allowed or required - set(data):
        raise ValueError("备份缺少必须的完整资料表或包含不支持的数据表")
    if manifest is not None:
        _manifest_binding(payload, manifest)
    schema = _schema(conn, data)
    decoded, _ = _decoded_data(data, schema)
    indexes = _check_graph(decoded, schema, payload["project_id"], full=full)
    _check_content(decoded, indexes)
    order = _table_order(decoded, schema)
    # Exercise CHECK constraints and triggers only in an empty isolated database.
    definitions = [row for row in conn.execute(
        "SELECT type,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' AND type IN ('table','index','trigger') "
        "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,rowid")
        if row[1] in allowed]
    try:
        with closing(sqlite3.connect(":memory:")) as scratch, scratch:
            scratch.execute("PRAGMA foreign_keys=ON")
            for _, _, statement in definitions:
                scratch.execute(statement)
            scratch.execute("BEGIN")
            scratch.execute("PRAGMA defer_foreign_keys=ON")
            _insert_data(scratch, decoded, order)
            if scratch.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("备份中的资料引用校验失败")
    except sqlite3.Error as error:
        raise ValueError("备份资料不满足当前版本的数据约束，未恢复任何内容") from error
    return {"status": "compatible", "defaults_applied": [],
            "absent_known_additive_tables": sorted((KNOWN_ADDITIVE_TABLES & allowed) - set(data))}


def _remap_reference(value, mapping, identities, target, *, names=False):
    if isinstance(value, str):
        if value in identities.get(target, set()):
            return mapping[value]
        # Scope declarations permit a character or branch name as literal text.
        if names:
            return value
    return copy.deepcopy(value)


def remap_value(value, mapping, identities=None, key=""):
    """Map only declared identity fields; prose and unknown fields stay exact."""
    identities = identities or {}
    if key in {"request_audit", "payload", "messages", "structured"} or (key in JSON_TEXT_SUBTREES and isinstance(value, str)):
        return copy.deepcopy(value)
    if key == "reference_passages" and isinstance(value, list):
        result = copy.deepcopy(value)
        for passage in result:
            passage["document_id"] = mapping[passage["document_id"]]
            passage["section_id"] = mapping[passage["section_id"]]
            passage["project_id"] = mapping[passage["project_id"]]
            passage["branch_id"] = mapping[passage["branch_id"]]
            passage["id"] = (passage["section_id"] + ":" + str(passage["section_start"])
                             + ":" + str(passage["section_end"]))
        return result
    if key in JSON_REFERENCES:
        return _remap_reference(value, mapping, identities, JSON_REFERENCES[key])
    if key in JSON_REFERENCE_LISTS and isinstance(value, list):
        return [_remap_reference(item, mapping, identities, JSON_REFERENCE_LISTS[key], names=True)
                for item in value]
    if isinstance(value, list):
        return [remap_value(item, mapping, identities, key) for item in value]
    if isinstance(value, dict):
        result = {name: remap_value(item, mapping, identities, name) for name, item in value.items()}
        if isinstance(value.get("id"), str) and value["id"] in mapping:
            # id is structural only on objects that explicitly declare their kind.
            kind = value.get("kind") or value.get("material_kind")
            target = JSON_KIND_IDENTITIES.get(kind) if isinstance(kind, str) else None
            target = JSON_OBJECT_IDENTITIES.get(key, target)
            if target and value["id"] in identities.get(target, set()):
                result["id"] = mapping[value["id"]]
        return result
    return copy.deepcopy(value)


def remap_ai_record(value, mapping, identities=None, key=""):
    return remap_value(value, mapping, identities, key)


def transplant(conn, payload, new_project, title=None):
    """Restore as an independent book, keeping the current book and its history."""
    started = time.perf_counter()
    validate_payload(conn, payload)
    if not isinstance(new_project, str) or not new_project or new_project == payload["project_id"]:
        raise ValueError("恢复副本需要独立的作品身份")
    if conn.execute("SELECT 1 FROM project WHERE id=?", (new_project,)).fetchone():
        raise ValueError("恢复副本的作品身份已存在")
    schema = _schema(conn, payload["tables"])
    data, _ = _decoded_data(payload["tables"], schema)
    if title is None or title == "":
        original_title = data["project"][0].get("name")
        restored_title = original_title[:PROJECT_TITLE_LIMIT - len(RESTORE_TITLE_SUFFIX)] + RESTORE_TITLE_SUFFIX
    else:
        if not isinstance(title, str):
            raise ValueError("作品名需要为文字")
        restored_title = title.strip()
        if not restored_title:
            raise ValueError("请填写作品名")
        if len(restored_title) > PROJECT_TITLE_LIMIT:
            raise ValueError("作品名不能超过200字")
    mapping = {payload["project_id"]: new_project}
    identities = {table: {row["id"] for row in rows if "id" in row} for table, rows in data.items()}
    for table, rows in data.items():
        for row in rows:
            if row.get("id") and row["id"] not in mapping:
                mapping[row["id"]] = table + "_" + uuid.uuid4().hex
    for table, rows in data.items():
        for row in rows:
            if table == "event_log":
                old_key = row["idempotency_key"]
                row["idempotency_key"] = mapping.setdefault(old_key, "restored_" + uuid.uuid4().hex)
            if table == "app_assistant_request":
                old_token = row["token_hash"]
                row["token_hash"] = hashlib.sha256(
                    (new_project + ":" + old_token).encode("utf-8")
                ).hexdigest()
                mapping.setdefault(old_token, row["token_hash"])
    for table, fields in HISTORICAL_ID_COLUMNS.items():
        for row in data.get(table, []):
            for field in fields:
                if row[field] not in mapping:
                    mapping[row[field]] = "restored_history_" + uuid.uuid4().hex
    for table, rows in data.items():
        reference_columns = {fk[3] for fk in schema[table]["foreign_keys"]}
        reference_columns.update(EXTRA_REFERENCES.get(table, {}))
        reference_columns.update(HISTORICAL_ID_COLUMNS.get(table, set()))
        if table == "prov_entity":
            reference_columns.add("entity_ref")
        if table == "prov_edge":
            reference_columns.update({"subject_id", "object_id"})
        reference_columns.add("id")
        for row in rows:
            if table == "app_model_settings":
                row["settings_json"] = _portable_model_settings(row["settings_json"])
            for key, value in list(row.items()):
                if key.endswith("_json") and isinstance(value, str):
                    if table in PLANNING_TABLES or table in AGENT_TABLES or table == "app_model_settings":
                        continue
                    if table == "event_log" and row["event_type"] == "project.portable_restored":
                        continue
                    parsed = _strict_json(value)
                    if (table, key) in UNCHANGED_JSON:
                        changed = parsed
                    elif table == "app_timeline_event" and key == "entity_ids_json":
                        changed = [_remap_reference(item, mapping, identities, "entity") for item in parsed]
                    elif table == "app_entity_scope" and key == "scope_json":
                        changed = remap_value(decode_scope(value), mapping, identities)
                    elif table == "ai_run" and key == "context_item_ids_json":
                        changed = [mapping.get(item, item) if isinstance(item, str) else item for item in parsed]
                    else:
                        changed = remap_value(parsed, mapping, identities)
                        if (table in {"app_chapter_analysis", "app_story_review"}
                                and key == "payload_json" and isinstance(changed, dict)
                                and changed.get("id") in identities[table]):
                            changed["id"] = mapping[changed["id"]]
                    row[key] = json.dumps(changed, ensure_ascii=False, allow_nan=False)
                elif key in reference_columns and isinstance(value, str):
                    row[key] = mapping.get(value, value)
                elif key in ACTOR_TEXT_COLUMNS.get(table, set()) and value in identities.get("actor", set()):
                    row[key] = mapping[value]
            if table == "project":
                row["name"] = restored_title
            if table == "app_ai_job":
                context = _strict_json(row["context_json"])
                context["_restored_read_only"] = True
                row["context_json"] = json.dumps(context, ensure_ascii=False)
                if row["status"] in {"running", "queued"}:
                    row.update(status="failed", error="从备份恢复；旧请求不会自动再次调用模型",
                               cancel_requested=1)
            if table == "ai_run":
                consent = _strict_json(row["consent_json"])
                consent["restored_read_only"] = True
                row["consent_json"] = json.dumps(consent, ensure_ascii=False)
                if row["status"] in {"running", "queued"}:
                    row.update(status="unknown", error="备份中的外部请求状态未知，未重复执行")
            if table == "app_planning_workflow":
                row.update(status="canceled", cancel_reason=PLANNING_RESTORE_REASON)
                row["version"] = int(row["version"]) + 1
            if table == "app_planning_attempt" and row["status"] in {"queued", "running", "unknown"}:
                row.update(status="unknown", error="备份中的规划模型请求费用与执行状态未知，未重复执行。")
            if table == "app_agent_session":
                row.update(status="restored", restored_read_only=1)
            if table == "app_agent_chat_session":
                row["restored_read_only"] = 1
            if table == "app_agent_chat_turn" and row["status"] in {"queued", "running"}:
                row["status"] = "failed"
            if table == "app_agent_chat_action" and row["status"] == "confirming":
                row["status"] = "proposed"
    order = _table_order(data, schema)
    branches = data.get("branch") or []
    branch = next((row["id"] for row in branches if row["status"] == "active"), None)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    savepoint = "complete_restore_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + savepoint)
    deferred = conn.execute("PRAGMA defer_foreign_keys").fetchone()[0]
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        _insert_data(conn, data, order)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("恢复后的资料引用校验失败")
    except BaseException:
        conn.execute("ROLLBACK TO " + savepoint)
        conn.execute("RELEASE " + savepoint)
        raise
    else:
        conn.execute("RELEASE " + savepoint)
    finally:
        conn.execute("PRAGMA defer_foreign_keys=" + ("ON" if deferred else "OFF"))
    return {"project_id": new_project, "branch_id": branch, "identity_map": mapping,
            "row_counts": {key: len(value) for key, value in data.items()},
            "rto_seconds": round(time.perf_counter() - started, 6)}
