"""Verified, project-local model connections with one-use probe receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import moyu_settings as settings
import moyu_provider
from moyu_provider import prepare_chat_request


_ALIASES = {"summary": "chapter_analysis", "check": "semantic_review"}
_PENDING_LIMIT = 12
_CONNECTION_LIMIT = 100


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("连接测试时间无效。")
    return parsed.astimezone(timezone.utc)


def _payload(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    saved = settings._saved(conn, project)
    return dict(saved) if saved is not None else settings._environment()


def _connections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("connections", [])
    if (
        not isinstance(values, list)
        or len(values) > _CONNECTION_LIMIT
        or any(not isinstance(value, dict) for value in values)
    ):
        raise ValueError("模型连接库格式无效。")
    identifiers = [value.get("id") for value in values]
    if any(not isinstance(value, str) or not value for value in identifiers) or len(set(identifiers)) != len(values):
        raise ValueError("模型连接库身份无效。")
    return values


def _find(payload: dict[str, Any], identifier: str) -> dict[str, Any] | None:
    return next((entry for entry in _connections(payload) if entry["id"] == identifier), None)


def _local(base_url: str) -> bool:
    try:
        return urlsplit(base_url).hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    except (AttributeError, ValueError):
        return False


def _fingerprint(key: str) -> str | None:
    return hashlib.sha256(key.encode()).hexdigest() if key else None


def _signature(base_url: str, model: str, fingerprint: str | None,
               options: dict[str, Any] | None = None, *, legacy: bool = False) -> str:
    fields = {"base_url": base_url, "model": model, "credential_fingerprint": fingerprint}
    if not legacy:
        fields.update(settings._connection_options(options or {}))
    encoded = json.dumps(
        fields,
        sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _signature_matches(signature: Any, base_url: str, model: str, fingerprint: str | None,
                       options: dict[str, Any]) -> bool:
    if not isinstance(signature, str):
        return False
    normalized = settings._connection_options(options)
    if hmac.compare_digest(signature, _signature(base_url, model, fingerprint, normalized)):
        return True
    return (normalized == settings._connection_options({})
            and hmac.compare_digest(signature, _signature(base_url, model, fingerprint, legacy=True)))


def _private_key(conn: sqlite3.Connection, project: str, reference: Any) -> str:
    if reference is not None and (
        not isinstance(reference, str)
        or not reference.startswith(settings._project_prefix(conn, project) + "-")
    ):
        raise ValueError("连接密钥来自其他作品或数据库，请重新测试。")
    return settings._read_key(reference)


def _verified(conn: sqlite3.Connection, project: str, entry: dict[str, Any]) -> tuple[bool, str]:
    try:
        key = _private_key(conn, project, entry.get("_credential_ref"))
        probe = settings._public_probe(entry.get("last_probe"))
        valid = (
            _signature_matches(entry.get("_verified_signature"),
                               entry.get("base_url", ""), entry.get("model", ""), _fingerprint(key), entry)
            and isinstance(probe, dict)
            and probe.get("success") is True
            and (bool(key) or _local(entry.get("base_url", "")))
        )
        return bool(valid), key
    except (OSError, TypeError, ValueError):
        return False, ""


def _public_connection(conn: sqlite3.Connection, project: str, entry: dict[str, Any]) -> dict[str, Any]:
    verified, key = _verified(conn, project, entry)
    probe = settings._public_probe(entry.get("last_probe"))
    if not verified and isinstance(probe, dict) and probe.get("success"):
        probe = {**probe, "success": False, "configuration_stale": True,
                 "error_code": "connection_verification_missing",
                 "error": "连接凭据或验证记录已变化，请重新测试后保存。"}
    return {
        "id": entry["id"], "name": entry.get("name", ""), "base_url": entry.get("base_url", ""),
        "model": entry.get("model", ""), "api_key_configured": bool(key), "verified": verified,
        "last_probe": probe, "revision": entry.get("revision", 0),
        **settings._connection_options(entry),
    }


def _legacy_key(conn: sqlite3.Connection, project: str, payload: dict[str, Any]) -> str:
    if settings._saved(conn, project) is None:
        return os.environ.get("MOYU_AI_API_KEY", "")
    return _private_key(conn, project, payload.get("_credential_ref"))


def public_connections(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    payload = _payload(conn, project)
    legacy = None
    if payload.get("base_url") or payload.get("model") or payload.get("_credential_ref"):
        try:
            key_configured = bool(_legacy_key(conn, project, payload))
        except (OSError, ValueError):
            key_configured = False
        legacy = {"base_url": payload.get("base_url", ""), "model": payload.get("model", ""),
                  "api_key_configured": key_configured, **settings._connection_options(payload)}
    return {
        "connections": [_public_connection(conn, project, entry) for entry in _connections(payload)],
        "legacy": legacy, "connection_workflow": payload.get("connection_workflow") is True,
    }


def _active_pending(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pending = payload.get("_pending_connection_probes", [])
    if not isinstance(pending, list):
        raise ValueError("连接测试记录格式无效。")
    now = _instant(_now())
    active = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        try:
            if _instant(item["expires_at"]) > now:
                active.append(item)
        except (KeyError, TypeError, ValueError):
            continue
    return active[-_PENDING_LIMIT:]


def _legacy_revision(conn: sqlite3.Connection, project: str, payload: dict[str, Any]) -> str:
    key = _legacy_key(conn, project, payload)
    return _signature(payload.get("base_url", ""), payload.get("model", ""), _fingerprint(key), payload)


def _draft_credentials(conn: sqlite3.Connection, project: str, payload: dict[str, Any],
                       data: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, str]:
    identifier = data.get("id")
    if identifier == "":
        identifier = None
    if identifier is not None and (not isinstance(identifier, str) or len(identifier) > 200):
        raise ValueError("连接身份无效。")
    entry = _find(payload, identifier) if identifier and identifier != "legacy" else None
    if identifier and identifier != "legacy" and entry is None:
        raise ValueError("要修改的模型连接不存在。")
    supplied = data.get("api_key")
    if supplied is not None and not isinstance(supplied, str):
        raise ValueError("密钥必须为文字。")
    if supplied is not None:
        key = supplied
    elif entry is not None:
        key = _private_key(conn, project, entry.get("_credential_ref"))
    elif identifier == "legacy":
        key = _legacy_key(conn, project, payload)
    else:
        key = ""
    return identifier, entry, key


def list_connection_models(conn: sqlite3.Connection, project: str,
                           data: dict[str, Any]) -> dict[str, Any]:
    """Read the draft provider's inventory without saving or testing a connection."""
    if not isinstance(data, dict) or set(data) - {"id", "base_url", "api_key"}:
        raise ValueError("模型列表查询包含未支持的字段。")
    payload = _payload(conn, project)
    _, _, key = _draft_credentials(conn, project, payload, data)
    base_url = settings._text(data.get("base_url"), "服务地址", 2048).rstrip("/")
    if not base_url:
        raise ValueError("请先填写服务地址。")
    if not key and not _local(base_url):
        raise ValueError("远程连接需要填写密钥后获取模型。")
    result = moyu_provider.list_models({"base_url": base_url, "api_key": key})
    return moyu_provider._redact(
        {"models": result["models"], "endpoint": result["endpoint"]}, key)


def probe_connection(conn: sqlite3.Connection, project: str, data: dict[str, Any],
                     cancel_event=None) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) - {
            "id", "name", "base_url", "model", "api_key", "output_token_parameter", "include_usage"}:
        raise ValueError("连接测试包含未支持的字段。")
    initial = _payload(conn, project)
    identifier, entry, key = _draft_credentials(conn, project, initial, data)
    if identifier is None and len(_connections(initial)) >= _CONNECTION_LIMIT:
        raise ValueError("模型连接数量已达到上限，请先删除不用的连接。")
    base_url = settings._text(data.get("base_url"), "服务地址", 2048).rstrip("/")
    model = settings._text(data.get("model"), "模型名称", 200)
    if not base_url or not model:
        raise ValueError("请填写模型地址和名称后测试。")
    name = settings._text(data.get("name", entry.get("name", "") if entry else ""), "连接名称", 100)
    option_source = entry if entry is not None else initial if identifier == "legacy" else {}
    options = settings._connection_options({**option_source, **data})
    if not key and not _local(base_url):
        raise ValueError("远程连接需要填写密钥后测试。")
    revision = entry.get("revision") if entry is not None else None
    legacy_revision = _legacy_revision(conn, project, initial) if identifier == "legacy" else None
    fingerprint = _fingerprint(key)
    config = {
        "base_url": base_url, "model": model, "api_key": key, "task_models": {},
        **settings._provider_parameters(initial),
        **options,
        "credential_ref": None, "credential_fingerprint": fingerprint, "credential_source": "private_file",
    }
    config["task_models"] = {}
    prepare_chat_request(config, [{"role": "user", "content": "连接正常"}],
                         task="probe", max_tokens=settings.PROBE_MAX_TOKENS, parameters={"temperature": 0})

    def unchanged(connection):
        latest = _payload(connection, project)
        if identifier == "legacy":
            try:
                return _legacy_revision(connection, project, latest) == legacy_revision
            except (OSError, ValueError):
                return False
        if identifier:
            found = _find(latest, identifier)
            return found is not None and found.get("revision") == revision
        return True

    result = settings.probe_settings(
        conn, project, cancel_event=cancel_event, configuration_override=config,
        persist=False, configuration_guard=unchanged,
    )
    response = {"probe": result["probe"], "ai_run_id": result["ai_run_id"],
                "settings": result["settings"]}
    if result["probe"].get("success") is not True:
        return response
    latest = _payload(conn, project)
    if settings._saved(conn, project) is None:
        inherited_key = os.environ.get("MOYU_AI_API_KEY", "")
        if inherited_key:
            latest["_credential_ref"] = settings._store_key(conn, project, inherited_key)
    reference = settings._store_key(conn, project, key) if key else None
    token = secrets.token_urlsafe(32)
    created_at = _now()
    pending = {
        "token_hash": hashlib.sha256(token.encode()).hexdigest(), "created_at": created_at,
        "expires_at": (_instant(created_at) + timedelta(minutes=30)).isoformat(),
        "project_id": project, "project_prefix": settings._project_prefix(conn, project),
        "existing_id": identifier, "existing_revision": revision, "legacy_revision": legacy_revision,
        "name": name, "base_url": base_url, "model": model, "_credential_ref": reference,
        "credential_fingerprint": fingerprint,
        "_verified_signature": _signature(base_url, model, fingerprint, options),
        **options,
        "last_probe": result["probe"],
    }
    latest["_pending_connection_probes"] = (_active_pending(latest) + [pending])[-_PENDING_LIMIT:]
    settings._write_settings(conn, project, latest)
    response["verification_token"] = token
    response["settings"] = settings.public_settings(conn, project)
    return response


def save_connection(conn: sqlite3.Connection, project: str, data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) - {"verification_token", "name"}:
        raise ValueError("保存连接包含未支持的字段，请重新测试后保存。")
    token = data.get("verification_token")
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        raise ValueError("请先测试连接，成功后再保存。")
    conn.execute("UPDATE app_state_version SET version=version WHERE project_id=?", (project,))
    payload = _payload(conn, project)
    pending = _active_pending(payload)
    digest = hashlib.sha256(token.encode()).hexdigest()
    receipt = next((item for item in pending
                    if isinstance(item.get("token_hash"), str)
                    and hmac.compare_digest(item["token_hash"], digest)), None)
    if receipt is None:
        raise ValueError("连接测试凭证已过期或已使用，请重新测试。")
    if (receipt.get("project_id") != project
            or receipt.get("project_prefix") != settings._project_prefix(conn, project)):
        raise ValueError("连接测试凭证不属于当前作品。")
    key = _private_key(conn, project, receipt.get("_credential_ref"))
    fingerprint = _fingerprint(key)
    if (
        fingerprint != receipt.get("credential_fingerprint")
        or (not key and not _local(receipt.get("base_url", "")))
        or not _signature_matches(receipt.get("_verified_signature"),
                                  receipt.get("base_url", ""), receipt.get("model", ""), fingerprint, receipt)
        or not (settings._public_probe(receipt.get("last_probe")) or {}).get("success")
    ):
        raise ValueError("测试所用凭据已变化，请重新测试连接。")
    identifier = receipt.get("existing_id")
    existing = _find(payload, identifier) if identifier and identifier != "legacy" else None
    if identifier == "legacy":
        if not _signature_matches(receipt.get("legacy_revision"), payload.get("base_url", ""),
                                  payload.get("model", ""), _fingerprint(_legacy_key(conn, project, payload)),
                                  payload):
            raise ValueError("原连接在测试后发生变化，请重新测试。")
    elif identifier and (existing is None or existing.get("revision") != receipt.get("existing_revision")):
        raise ValueError("连接在测试后发生变化，请重新测试。")
    entries = _connections(payload)
    if existing is None and len(entries) >= _CONNECTION_LIMIT:
        raise ValueError("模型连接数量已达到上限，请先删除不用的连接。")
    name = settings._text(data.get("name", receipt.get("name", "")), "连接名称", 100)
    previous_revision = existing.get("revision", 0) if existing else 0
    if isinstance(previous_revision, bool) or not isinstance(previous_revision, int):
        previous_revision = 0
    entry = {
        "id": existing["id"] if existing else "connection_" + uuid.uuid4().hex,
        "name": name or receipt["model"][:100], "base_url": receipt["base_url"], "model": receipt["model"],
        "_credential_ref": receipt.get("_credential_ref"), "last_probe": receipt["last_probe"],
        "_verified_signature": receipt["_verified_signature"],
        **settings._connection_options(receipt),
        "revision": previous_revision + 1,
    }
    payload["connections"] = [entry if item["id"] == entry["id"] else item for item in entries]
    if existing is None:
        payload["connections"].append(entry)
    payload["_pending_connection_probes"] = [item for item in pending if item is not receipt]
    payload.setdefault("connection_workflow", False)
    payload.setdefault("default_connection_id", "")
    payload.setdefault("task_connections", {})
    settings._write_settings(conn, project, payload)
    return {"connection": _public_connection(conn, project, entry),
            "settings": settings.public_settings(conn, project)}


def remove_connection(conn: sqlite3.Connection, project: str, identifier: str) -> dict[str, Any]:
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("连接身份无效。")
    conn.execute("UPDATE app_state_version SET version=version WHERE project_id=?", (project,))
    payload = _payload(conn, project)
    if _find(payload, identifier) is None:
        raise ValueError("模型连接不存在。")
    assignments = payload.get("task_connections") or {}
    if payload.get("default_connection_id") == identifier or identifier in assignments.values():
        raise ValueError("这个连接仍用于写作，请先取消默认或用途分配后再删除。")
    payload["connections"] = [item for item in _connections(payload) if item["id"] != identifier]
    payload["_pending_connection_probes"] = [
        item for item in _active_pending(payload) if item.get("existing_id") != identifier
    ]
    settings._write_settings(conn, project, payload)
    return {"settings": settings.public_settings(conn, project)}


def validate_assignments(conn: sqlite3.Connection, project: str, payload: dict[str, Any],
                         data: dict[str, Any]) -> dict[str, Any]:
    default = data.get("default_connection_id", payload.get("default_connection_id", ""))
    tasks = data.get("task_connections", payload.get("task_connections", {}))
    if not isinstance(default, str) or not isinstance(tasks, dict) or set(tasks) - settings.TASKS:
        raise ValueError("用途连接设置无效。")
    normalized = {}
    for task, identifier in tasks.items():
        if not isinstance(identifier, str):
            raise ValueError("请选择已测试保存的模型连接。")
        if identifier:
            normalized[task] = identifier
    for identifier in {default, *normalized.values()} - {""}:
        entry = _find(payload, identifier)
        if entry is None or not _verified(conn, project, entry)[0]:
            raise ValueError("用途只能选择当前作品中已测试保存且凭据有效的连接。")
    return {"default_connection_id": default, "task_connections": normalized}


def _route(conn: sqlite3.Connection, project: str, payload: dict[str, Any],
           identifier: str) -> dict[str, Any] | None:
    if not identifier:
        return None
    entry = _find(payload, identifier)
    if entry is None:
        return {"connection_id": identifier, "_invalid": True}
    verified, key = _verified(conn, project, entry)
    return {
        "connection_id": entry["id"], "connection_revision": entry.get("revision"),
        "base_url": entry["base_url"], "model": entry["model"],
        "credential_ref": entry.get("_credential_ref"), "credential_fingerprint": _fingerprint(key),
        "credential_source": "private_file", "_verified_signature": entry.get("_verified_signature"),
        **settings._connection_options(entry),
        "_invalid": not verified,
    }


def routing_config(conn: sqlite3.Connection, project: str, payload: dict[str, Any]) -> dict[str, Any]:
    default = _route(conn, project, payload, payload.get("default_connection_id") or "")
    tasks = payload.get("task_connections") or {}
    if not isinstance(tasks, dict) or set(tasks) - settings.TASKS:
        raise ValueError("用途连接设置无效。")
    routes = {task: _route(conn, project, payload, identifier) for task, identifier in tasks.items() if identifier}
    config = {"base_url": "", "model": "", "api_key": "", "credential_ref": None,
              "credential_fingerprint": None, "credential_source": "private_file",
              **settings._provider_parameters(payload), "task_models": {},
              "_connection_routes": {"default": default, "tasks": routes},
              "connection_workflow": True}
    if default and not default.get("_invalid"):
        config.update({key: value for key, value in default.items()
                       if key not in {"_verified_signature", "_invalid"}})
        config["api_key"] = _private_key(conn, project, default.get("credential_ref"))
    return config


def resolve_task_config(config: dict[str, Any], task: str) -> dict[str, Any]:
    """Keep purposes independent; legacy parent purposes fill only unset children."""
    routing = config.get("_connection_routes")
    if not isinstance(routing, dict) or not isinstance(routing.get("tasks"), dict):
        raise ValueError("用途连接路由无效，请重新选择已验证的模型。")
    routes = routing["tasks"]
    route = routes.get(task)
    if route is None and task in _ALIASES.values():
        alias = next(name for name, value in _ALIASES.items() if value == task)
        route = routes.get(alias)
    if route is None:
        route = routing.get("default")
    if not isinstance(route, dict):
        raise ValueError("这个用途尚未选择模型，请在设置中选择已测试保存的连接。")
    if route.get("_invalid"):
        raise ValueError("这个用途所选连接已失效，请重新测试并保存后继续。")
    key = settings._read_key(route.get("credential_ref"))
    fingerprint = _fingerprint(key)
    if (
        fingerprint != route.get("credential_fingerprint")
        or (not key and not _local(route.get("base_url", "")))
        or not _signature_matches(route.get("_verified_signature"),
                                  route.get("base_url", ""), route.get("model", ""), fingerprint, route)
    ):
        raise ValueError("所选模型连接凭据已变化，请重新测试后继续。")
    resolved = {key: value for key, value in config.items() if key != "_connection_routes"}
    resolved.update({key: value for key, value in route.items()
                     if key not in {"_verified_signature", "_invalid"}})
    resolved.update(api_key=key, task_models={})
    return resolved
