"""Bounded ZIP previews and durable, project-scoped restore receipts."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import moyu_backups as backups


PREVIEW_SECONDS = 1800
MAX_PENDING = 2
MAX_PENDING_BYTES = 64 * 1024 * 1024
MAX_CANCELED = 512
_PREVIEWS = {}
_CANCELED = {}
_LOCK = threading.Lock()
_TOKEN = re.compile(r"restore_preview_[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{64}")


class RestoreConflict(ValueError):
    status = 409


def read_request(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("恢复请求包含重复资料，请重新选择备份。")
            result[key] = value
        return result

    def finite(value):
        raise ValueError("恢复请求包含无效数值。")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
        if not isinstance(value, dict):
            raise ValueError("恢复请求需要完整文件资料。")
        for key, item in value.items():
            key.encode("utf-8", errors="strict")
            if isinstance(item, str):
                item.encode("utf-8", errors="strict")
        return value
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("恢复请求损坏，请重新选择完整备份 ZIP。") from None


def _database(conn):
    filename = conn.execute("PRAGMA database_list").fetchone()[2]
    return str(Path(filename).resolve()) if filename else id(conn)


def _scope(scope):
    return {"project_id": scope["project_id"], "branch_id": scope["branch_id"]}


def _prune():
    expired = [key for key, entry in _PREVIEWS.items()
               if time.monotonic() - entry["created"] > PREVIEW_SECONDS
               and not entry["lock"].locked()]
    for key in expired:
        _PREVIEWS.pop(key, None)
    for key in [key for key, entry in _CANCELED.items()
                if time.monotonic() - entry["created"] > PREVIEW_SECONDS]:
        _CANCELED.pop(key, None)


def _preview_id(data):
    token = data.get("preview_id")
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("恢复预览无效，请重新选择完整备份 ZIP。")
    return token


def preview(conn, data, scope):
    if set(data) not in ({"filename", "data_base64"},
                         {"filename", "data_base64", "preview_id"}):
        raise ValueError("请选择完整备份 ZIP，文件资料不能由页面另行修改。")
    token = _preview_id(data) if "preview_id" in data else "restore_preview_" + uuid.uuid4().hex
    filename = data["filename"]
    if (not isinstance(filename, str) or not 1 <= len(filename) <= 500
            or not filename.lower().endswith(".zip")
            or any(ord(char) < 32 for char in filename)):
        raise ValueError("请选择扩展名为 ZIP 的完整作品备份。")
    encoded = data["data_base64"]
    max_encoded = ((backups.MAX_ARCHIVE_BYTES + 2) // 3) * 4
    if not isinstance(encoded, str) or len(encoded) > max_encoded:
        raise ValueError("完整备份 ZIP 超过支持大小，最多 32 MiB。")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("完整备份文件传输损坏，请重新选择 ZIP。") from None
    if not raw or len(raw) > backups.MAX_ARCHIVE_BYTES:
        raise ValueError("完整备份 ZIP 为空或超过支持大小，最多 32 MiB。")
    database = _database(conn)
    with _LOCK:
        _prune()
        if token in _CANCELED:
            raise RestoreConflict("这次恢复预览已关闭，请重新选择完整备份 ZIP。")
        entry = _PREVIEWS.get(token)
        if entry:
            if (entry["scope"] != _scope(scope) or entry["database"] != database
                    or entry["raw"] != raw or entry["filename"] != filename):
                raise RestoreConflict("这次预览的文件或作品已变化，请重新选择完整备份。")
        elif (len(_PREVIEWS) >= MAX_PENDING
                or sum(len(entry["raw"]) for entry in _PREVIEWS.values()) + len(raw) > MAX_PENDING_BYTES):
            raise ValueError("已有两份待确认的恢复预览，请先完成或关闭预览。")
        else:
            entry = {
                "raw": raw, "filename": filename, "scope": _scope(scope), "database": database,
                "created": time.monotonic(), "lock": threading.Lock(), "stage": "validating",
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=PREVIEW_SECONDS)).isoformat(),
            }
            _PREVIEWS[token] = entry
    if not entry["lock"].acquire(timeout=30):
        raise RestoreConflict("完整备份仍在校验，请稍后确认预览。")
    try:
        with _LOCK:
            if _PREVIEWS.get(token) is not entry:
                raise RestoreConflict("这次恢复预览已关闭，请重新选择完整备份 ZIP。")
            if entry.get("preview"):
                return entry["preview"]
        archive = backups.read_bytes(raw)
        compatibility = backups.validate_payload(conn, archive["payload"], manifest=archive["manifest"])
        tables = archive["payload"]["tables"]
        project = tables["project"][0]
        source_title = project["name"]
        title = source_title[:backups.PROJECT_TITLE_LIMIT - len(backups.RESTORE_TITLE_SUFFIX)]
        result = {
            "preview_id": token, "filename": filename, "archive_sha256": archive["archive_sha256"],
            "project_sha256": archive["sha256"], "expires_at": entry["expires_at"],
            "source": {
                "title": source_title, "created_at": archive["manifest"]["created_at"],
                "archive_format_version": archive["payload"]["version"],
                "schema_version": project["schema_version"],
            },
            "restore_title": title + backups.RESTORE_TITLE_SUFFIX,
            "counts": {
                "branches": len(tables["branch"]),
                "chapters": sum(row.get("kind") == "chapter" for row in tables["document_node"]),
                "revisions": len(tables["document_revision"]),
                "reference_documents": len(tables.get("app_reference_document", [])),
                "jobs": len(tables.get("app_ai_job", [])),
                "planning_workflows": len(tables.get("app_planning_workflow", [])),
            },
            "row_counts": {table: len(rows) for table, rows in tables.items()},
            "compatibility": compatibility,
            "warnings": ["旧任务和规划只供阅读，不会再次调用模型。",
                         "备份不含可用密钥，恢复作品需要重新配置。"],
        }
        with _LOCK:
            if _PREVIEWS.get(token) is not entry:
                raise RestoreConflict("这次恢复预览已关闭，请重新选择完整备份 ZIP。")
            entry.update(stage="ready", preview=result)
        return result
    except Exception:
        with _LOCK:
            if _PREVIEWS.get(token) is entry and entry["stage"] == "validating":
                _PREVIEWS.pop(token)
        raise
    finally:
        entry["lock"].release()


def cancel(conn, data, scope):
    if set(data) != {"preview_id"}:
        raise ValueError("关闭恢复预览的资料无效。")
    token = _preview_id(data)
    with _LOCK:
        _prune()
        entry = _PREVIEWS.get(token)
        previous = _CANCELED.get(token)
        if previous and (previous["scope"] != _scope(scope)
                         or previous["database"] != _database(conn)):
            raise RestoreConflict("作品或分支已经切换，请关闭原来的恢复窗口。")
        if entry:
            if entry["scope"] != _scope(scope) or entry["database"] != _database(conn):
                raise RestoreConflict("作品或分支已经切换，请关闭原来的恢复窗口。")
            if entry["lock"].locked() and entry.get("stage") != "validating":
                raise RestoreConflict("恢复正在保存，请等待结果后打开副本。")
        if token not in _CANCELED and len(_CANCELED) >= MAX_CANCELED:
            raise ValueError("关闭的预览较多，请稍后重试，原作品未改动。")
        _CANCELED[token] = {
            "scope": _scope(scope), "database": _database(conn), "created": time.monotonic(),
        }
        if entry:
            _PREVIEWS.pop(token)
    return {"canceled": True}


def _receipt(conn, key, request_hash, scope):
    row = conn.execute(
        "SELECT event_type,payload_json FROM event_log WHERE idempotency_key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        raise RestoreConflict("已保存的恢复回执损坏，请在作品书架查找恢复副本。") from None
    if (row["event_type"] != "project.portable_restored"
            or not isinstance(payload, dict) or payload.get("target_scope") != _scope(scope)
            or payload.get("request_sha256") != request_hash):
        raise RestoreConflict("这次恢复已经保存，确认资料已变化，请打开原来的恢复副本。")
    result = payload.get("result")
    if not isinstance(result, dict) or not result.get("project_id") or not result.get("branch_id"):
        raise RestoreConflict("已保存的恢复身份缺失，请在作品书架查找恢复副本。")
    return result


def _restore_request(data, scope):
    if set(data) != {"preview_id", "archive_sha256", "title", "confirm"} or data.get("confirm") is not True:
        raise ValueError("请确认用完整备份建立独立作品。")
    token = _preview_id(data)
    archive_hash = data["archive_sha256"]
    if not isinstance(archive_hash, str) or not _SHA.fullmatch(archive_hash):
        raise ValueError("备份校验资料无效，请重新选择 ZIP。")
    title = data["title"]
    if (not isinstance(title, str) or not title.strip()
            or len(title) > backups.PROJECT_TITLE_LIMIT or any(ord(char) < 32 for char in title)):
        raise ValueError("作品名需要 1 到 200 字，不能包含换行或控制字符。")
    request_hash = hashlib.sha256(json.dumps(
        {**data, "target_scope": _scope(scope)}, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
    key = "portable_restore_" + token
    return token, archive_hash, title, request_hash, key


def status(conn, data, scope, callbacks):
    token, _archive_hash, _title, request_hash, key = _restore_request(data, scope)
    with _LOCK:
        _prune()
        entry = _PREVIEWS.get(token)
        if entry and (entry["scope"] != _scope(scope) or entry["database"] != _database(conn)):
            raise RestoreConflict("作品或分支已经切换，请回到原来的恢复窗口。")
    acquired = entry and entry["lock"].acquire(timeout=30)
    if entry and not acquired:
        raise RestoreConflict("恢复仍在保存，请稍后确认保存结果。")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            previous = _receipt(conn, key, request_hash, scope)
            if previous:
                return {"saved": True, "result": callbacks["response"](conn, previous)}
            with _LOCK:
                available = bool(_PREVIEWS.get(token) is entry and entry
                                 and time.monotonic() - entry["created"] <= PREVIEW_SECONDS)
            return {"saved": False, "preview_available": available}
        finally:
            conn.rollback()
    finally:
        if acquired:
            entry["lock"].release()


def apply(conn, data, scope, callbacks):
    token, archive_hash, title, request_hash, key = _restore_request(data, scope)
    previous = _receipt(conn, key, request_hash, scope)
    if previous:
        return 200, {**callbacks["response"](conn, previous), "idempotent": True}
    database = _database(conn)
    with _LOCK:
        _prune()
        entry = _PREVIEWS.get(token)
        if not entry:
            previous = _receipt(conn, key, request_hash, scope)
            if previous:
                return 200, {**callbacks["response"](conn, previous), "idempotent": True}
            raise ValueError("恢复预览已过期或服务已重启，请重新选择完整备份 ZIP。")
        if entry["scope"] != _scope(scope) or entry["database"] != database:
            raise RestoreConflict("作品或分支已经切换，请重新选择完整备份。")
    if not entry["lock"].acquire(timeout=30):
        raise RestoreConflict("恢复正在保存，请稍后重新打开已建立的副本。")
    try:
        previous = _receipt(conn, key, request_hash, scope)
        if previous:
            return 200, {**callbacks["response"](conn, previous), "idempotent": True}
        with _LOCK:
            if _PREVIEWS.get(token) is not entry:
                raise ValueError("恢复预览已经关闭，请重新选择完整备份 ZIP。")
        if time.monotonic() - entry["created"] > PREVIEW_SECONDS:
            raise ValueError("恢复预览已过期，请重新选择完整备份 ZIP。")
        raw = entry["raw"]
        if hashlib.sha256(raw).hexdigest() != archive_hash:
            raise RestoreConflict("完整备份校验不一致，请重新选择原 ZIP。")
        archive = backups.read_bytes(raw)
        backups.validate_payload(conn, archive["payload"], manifest=archive["manifest"])
        if conn.in_transaction:
            raise RestoreConflict("当前数据正在保存，请稍后重新确认恢复。")
        # The receipt and every restored row commit together; retries read the receipt.
        conn.execute("BEGIN IMMEDIATE")
        try:
            previous = _receipt(conn, key, request_hash, scope)
            if previous:
                result = {**callbacks["response"](conn, previous), "idempotent": True}
                conn.commit()
                return 200, result
            restored = backups.transplant(
                conn, archive["payload"], "book_" + uuid.uuid4().hex, title.strip())
            restored.update(archive_sha256=archive_hash, project_sha256=archive["sha256"])
            callbacks["record"](conn, key, {
                "target_scope": _scope(scope), "request_sha256": request_hash, "result": restored,
                "archive_sha256": archive_hash, "project_sha256": archive["sha256"],
            })
            result = {**callbacks["response"](conn, restored), "idempotent": False}
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        with _LOCK:
            if _PREVIEWS.get(token) is entry:
                _PREVIEWS.pop(token)
        return 201, result
    finally:
        entry["lock"].release()
