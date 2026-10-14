"""Verified, project-scoped complete archives and non-destructive recovery."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from moyu_records import decode_scope
from scripts.sqlite_backup import backup_database, verify_backup


SKIP_TABLES = {"migration_ledger", "app_backup_artifact", "app_search_document",
               "app_search_index_state", "app_search_fts", "index_checkpoint"}
PLANNING_TABLES = {"app_planning_workflow", "app_planning_attempt",
                   "app_planning_event", "app_planning_application"}
PLANNING_RESTORE_REASON = "从备份恢复的规划仅供阅读，请重新开始规划；旧模型请求不会自动再次执行。"
PROJECT_TITLE_LIMIT = 200
RESTORE_TITLE_SUFFIX = " · 恢复副本"


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
    settings = json.loads(raw)
    if not isinstance(settings, dict):
        raise ValueError("备份中的模型设置格式无效")
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
        with sqlite3.connect(snapshot) as source:
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


def read(path, project=None):
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != {"project.json", "manifest.json"}:
            raise ValueError("备份内容不完整")
        if sum(item.file_size for item in archive.infolist()) > 256_000_000:
            raise ValueError("备份超过支持的大小")
        raw = archive.read("project.json")
        manifest = json.loads(archive.read("manifest.json"))
    if hashlib.sha256(raw).hexdigest() != manifest.get("sha256"):
        raise ValueError("备份校验失败，原作品未改动")
    payload = json.loads(raw)
    if payload.get("format") != "moyu-complete-project" or payload.get("version") != 1:
        raise ValueError("不支持的备份格式")
    if project and (payload.get("project_id") != project or manifest.get("project_id") != project):
        raise ValueError("这份备份属于其他作品")
    if not isinstance(payload.get("tables"), dict):
        raise ValueError("备份缺少完整资料")
    if {key: len(value) for key, value in payload["tables"].items()} != manifest.get("row_counts"):
        raise ValueError("备份记录数量校验失败")
    return {"payload": payload, "manifest": manifest, "sha256": manifest["sha256"]}


def remap_value(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [remap_value(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(key, key): remap_value(item, mapping) for key, item in value.items()}
    return value


def remap_ai_record(value, mapping, key=""):
    # Request audits and generated prose are historical bytes, not identifiers.
    if key in {"request_audit", "text", "content", "body", "body_tail", "quote", "source_quote"}:
        return copy.deepcopy(value)
    if isinstance(value, list):
        return [remap_ai_record(item, mapping) for item in value]
    if isinstance(value, dict):
        return {mapping.get(name, name): remap_ai_record(item, mapping, name)
                for name, item in value.items()}
    return mapping.get(value, value) if isinstance(value, str) else value


def transplant(conn, payload, new_project, title=None):
    """Restore as an independent book, keeping the current book and its history."""
    started = time.perf_counter()
    data = copy.deepcopy(payload["tables"])
    allowed = set(tables(conn))
    if set(data) - allowed or "project" not in data or len(data["project"]) != 1:
        raise ValueError("备份包含不支持的数据表")
    if not isinstance(data["project"][0], dict):
        raise ValueError("备份记录损坏")
    if title is None or title == "":
        original_title = data["project"][0].get("name")
        if not isinstance(original_title, str):
            raise ValueError("备份中的作品名无效")
        restored_title = original_title[:PROJECT_TITLE_LIMIT - len(RESTORE_TITLE_SUFFIX)] + RESTORE_TITLE_SUFFIX
    else:
        if not isinstance(title, str):
            raise ValueError("作品名需要为文字")
        restored_title = title.strip()
        if not restored_title:
            raise ValueError("请填写作品名")
        if len(restored_title) > PROJECT_TITLE_LIMIT:
            raise ValueError("作品名不能超过200字")
    # Child planning records have no direct project column. Check their
    # workflow ownership before remapping any identity or inserting any row.
    planning_workflows = {row.get("id") for row in data.get("app_planning_workflow", [])
                          if isinstance(row, dict) and row.get("project_id") == payload["project_id"]}
    for table in PLANNING_TABLES - {"app_planning_workflow"}:
        for row in data.get(table, []):
            if row.get("workflow_id") not in planning_workflows:
                raise ValueError("备份中的规划记录不属于当前作品")
    mapping = {payload["project_id"]: new_project}
    for table, rows in data.items():
        if not isinstance(rows, list):
            raise ValueError("备份记录类型错误")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("备份记录损坏")
            if row.get("id") and row["id"] not in mapping:
                mapping[row["id"]] = table + "_" + uuid.uuid4().hex
            if table == "event_log":
                mapping[row["idempotency_key"]] = "restored_" + uuid.uuid4().hex
            if table == "app_assistant_request":
                mapping[row["token_hash"]] = hashlib.sha256(
                    (new_project + ":" + row["token_hash"]).encode("utf-8")
                ).hexdigest()
    for table, rows in data.items():
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        for row in rows:
            if set(row) != columns:
                raise ValueError("备份与当前版本的数据格式不一致")
            if table == "app_model_settings":
                # Also sanitize archives created before private references were
                # excluded. A same-device restore must not reuse the old key.
                row["settings_json"] = _portable_model_settings(row["settings_json"])
            for key, value in list(row.items()):
                if isinstance(value, dict) and set(value) == {"$blob"}:
                    row[key] = base64.b64decode(value["$blob"], validate=True)
                elif key.endswith("_json") and isinstance(value, str):
                    if table == "app_entity_scope" and key == "scope_json":
                        row[key] = json.dumps(remap_value(decode_scope(value), mapping), ensure_ascii=False)
                    elif table in PLANNING_TABLES:
                        # Preserve the historical frozen candidate/source and
                        # their hashes verbatim. They remain readable only;
                        # canceled workflows cannot confirm or re-dispatch.
                        json.loads(value)
                    elif table in {"ai_run", "app_ai_job"}:
                        row[key] = json.dumps(remap_ai_record(json.loads(value), mapping), ensure_ascii=False)
                    else:
                        row[key] = json.dumps(remap_value(json.loads(value), mapping), ensure_ascii=False)
                elif isinstance(value, str):
                    row[key] = mapping.get(value, value)
            if row.get("project_id") not in (None, new_project):
                raise ValueError("备份包含其他作品资料")
            if table == "project":
                row["name"] = restored_title
            if table == "app_ai_job":
                context = json.loads(row["context_json"])
                context["_restored_read_only"] = True
                row["context_json"] = json.dumps(context, ensure_ascii=False)
                if row["status"] in {"running", "queued"}:
                    row.update(status="failed", error="从备份恢复；旧请求不会自动再次调用模型",
                               cancel_requested=1)
            if table == "ai_run":
                consent = json.loads(row["consent_json"])
                consent["restored_read_only"] = True
                row["consent_json"] = json.dumps(consent, ensure_ascii=False)
                if row["status"] in {"running", "queued"}:
                    row.update(status="unknown", error="备份中的外部请求状态未知，未重复执行")
            if table == "app_planning_workflow":
                row.update(status="canceled", cancel_reason=PLANNING_RESTORE_REASON)
                row["version"] = int(row["version"]) + 1
            if table == "app_planning_attempt" and row["status"] in {"queued", "running", "unknown"}:
                row.update(status="unknown", error="备份中的规划模型请求费用与执行状态未知，未重复执行。")
    dependencies = {}
    for table in data:
        dependencies[table] = {r[2] for r in conn.execute(f'PRAGMA foreign_key_list("{table}")')
                               if r[2] in data and r[2] != table}
    remaining, done = set(data), set()
    conn.execute("PRAGMA defer_foreign_keys=ON")
    while remaining:
        ready = [table for table in data if table in remaining and dependencies[table] <= done]
        if not ready:
            raise ValueError("备份的资料依赖无法恢复")
        for table in ready:
            for row in data[table]:
                keys = list(row)
                placeholders = ",".join("?" for _ in keys)
                column_sql = ",".join(f'"{key}"' for key in keys)
                if table == "export_job" and row.get("status") == "success":
                    intended = row["status"]
                    row["status"] = "awaiting_confirmation"
                    conn.execute(f'INSERT INTO "{table}" ({column_sql}) VALUES({placeholders})',
                                 [row[key] for key in keys])
                    conn.execute("UPDATE export_job SET status=?,confirmed_by=? WHERE id=?",
                                 (intended, row["confirmed_by"], row["id"]))
                else:
                    conn.execute(f'INSERT INTO "{table}" ({column_sql}) VALUES({placeholders})',
                                 [row[key] for key in keys])
            done.add(table)
            remaining.remove(table)
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("恢复后的资料引用校验失败")
    branches = data.get("branch") or []
    branch = next((row["id"] for row in branches if row["status"] == "active"), None)
    if not branch:
        raise ValueError("备份缺少可用稿件分支")
    return {"project_id": new_project, "branch_id": branch, "identity_map": mapping,
            "row_counts": {key: len(value) for key, value in data.items()},
            "rto_seconds": round(time.perf_counter() - started, 6)}
