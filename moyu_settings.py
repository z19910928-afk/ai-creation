"""Project-local provider preferences and private file-based credentials."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moyu_provider import ProviderError, call_chat_completion, prepare_chat_request, usage_accounting


TASKS = {"continue", "polish", "outline", "scene", "summary", "check", "brainstorm",
         "chapter_analysis", "semantic_review"}
_REF = re.compile(r"^[a-f0-9]{64}-[a-f0-9]{32}$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _root() -> Path:
    configured = os.environ.get("MOYU_CREDENTIALS_DIR")
    directory = Path(configured).expanduser() if configured else Path.home() / ".moyu" / "credentials"
    if directory.is_symlink():
        raise ValueError("密钥目录不能为符号链接。")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    return directory


def _project_prefix(conn: sqlite3.Connection, project: str) -> str:
    location = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    return hashlib.sha256((location + "\0" + project).encode()).hexdigest()


def _store_key(conn: sqlite3.Connection, project: str, key: str) -> str:
    # A fresh reference makes database rollback preserve the previous credential.
    # Only the reference, never the key, is put in the database/JSON backup.
    reference = _project_prefix(conn, project) + "-" + uuid.uuid4().hex
    directory = _root()
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                     prefix=".key-", delete=False) as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump({"api_key": key}, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(directory / (reference + ".json"))
    return reference


def _read_key(reference: str | None) -> str:
    if not reference:
        return ""
    if not isinstance(reference, str) or not _REF.fullmatch(reference):
        raise ValueError("密钥引用无效。")
    path = _root() / (reference + ".json")
    if not path.exists():
        return ""
    if path.is_symlink():
        raise ValueError("密钥文件不能为符号链接。")
    metadata = path.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > 20_000:
        raise ValueError("密钥文件权限或大小无效。")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        payload = json.load(handle)
    key = payload.get("api_key")
    if not isinstance(key, str) or len(key) > 4096:
        raise ValueError("密钥文件内容无效。")
    return key


def _environment() -> dict[str, Any]:
    temperature = float(os.environ.get("MOYU_AI_TEMPERATURE", "0.65"))
    limit = int(os.environ.get("MOYU_AI_DAILY_QUOTA", "0") or 0)
    return {"base_url": os.environ.get("MOYU_AI_BASE_URL", "").strip().rstrip("/"),
            "model": os.environ.get("MOYU_AI_MODEL", "").strip(), "models": {},
            "styles": {"tone": "", "instructions": ""},
            "parameters": {"temperature": temperature, "max_tokens": 3000},
            "daily_limit": limit, "last_probe": None}


def _saved(conn: sqlite3.Connection, project: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT settings_json FROM app_model_settings WHERE project_id=?", (project,)).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    if not isinstance(payload, dict):
        raise ValueError("作品模型设置损坏。")
    return payload


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name}必须为{low}至{high}的整数。")
    return value


def _text(value: Any, name: str, length: int) -> str:
    if not isinstance(value, str) or len(value) > length:
        raise ValueError(f"{name}必须为不超过{length}字的文字。")
    return value.strip()


def provider_settings(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    saved = _saved(conn, project)
    payload = saved if saved is not None else _environment()
    key = _read_key(payload.get("_credential_ref")) if saved is not None else os.environ.get("MOYU_AI_API_KEY", "")
    parameters = dict(payload.get("parameters") or {})
    max_tokens = parameters.pop("max_tokens", 3000)
    models = payload.get("models") or {}
    task_models = {task: model for task, model in models.items() if model}
    if "summary" in task_models:
        task_models.setdefault("chapter_analysis", task_models["summary"])
    if "check" in task_models:
        task_models.setdefault("semantic_review", task_models["check"])
    return {"base_url": payload.get("base_url", ""), "model": payload.get("model", ""),
            "api_key": key, "task_models": task_models, "parameters": parameters,
            "max_tokens": max_tokens, "styles": payload.get("styles") or {},
            "daily_limit": payload.get("daily_limit", 0),
            "credential_ref": payload.get("_credential_ref"),
            "credential_fingerprint": hashlib.sha256(key.encode()).hexdigest() if key else None,
            "credential_source": "private_file" if saved is not None else "environment"}


def frozen_provider_settings(conn: sqlite3.Connection, project: str,
                             snapshot: dict[str, Any]) -> dict[str, Any]:
    """Resolve the original secret identity without forwarding a newer key."""
    if not isinstance(snapshot, dict) or "api_key" in snapshot:
        raise ValueError("保存的模型请求设置无效。")
    reference = snapshot.get("credential_ref")
    if reference:
        if not isinstance(reference, str) or not reference.startswith(_project_prefix(conn, project) + "-"):
            raise ValueError("任务密钥来自其他作品或数据库，需重新配置后再建任务。")
        key = _read_key(reference)
        if not key:
            raise ValueError("任务所用密钥文件缺失，请重新配置后再建任务。")
    elif snapshot.get("credential_source") == "environment":
        key = os.environ.get("MOYU_AI_API_KEY", "")
    else:
        key = ""
    fingerprint = hashlib.sha256(key.encode()).hexdigest() if key else None
    if snapshot.get("credential_fingerprint") != fingerprint and "credential_fingerprint" in snapshot:
        raise ValueError("任务所用密钥已变化，请重新建立任务。")
    return {**snapshot, "api_key": key}


def public_settings(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    saved = _saved(conn, project)
    payload = saved if saved is not None else _environment()
    config = provider_settings(conn, project)
    return {
        "base_url": payload.get("base_url", ""), "model": payload.get("model", ""),
        "models": payload.get("models") or {}, "styles": payload.get("styles") or {},
        "parameters": payload.get("parameters") or {},
        "daily_limit": payload.get("daily_limit", 0), "last_probe": payload.get("last_probe"),
        "configured": bool(config["base_url"] and config["model"]),
        "api_key_configured": bool(config["api_key"]),
        "api_key_masked": "已保存密钥" if config["api_key"] else "",
        "credential_storage": "local_private_file",
        "credential_backup": "密钥不包含在作品备份中，换设备后需重新填写。",
    }


def save_settings(conn: sqlite3.Connection, project: str, data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("设置必须为对象。")
    allowed = {"base_url", "model", "api_key", "models", "styles", "parameters",
               "daily_limit", "clear_api_key"}
    if set(data) - allowed:
        raise ValueError("设置包含未支持的字段。")
    original = _saved(conn, project)
    current = dict(original) if original is not None else _environment()
    base_url = _text(data.get("base_url", current["base_url"]), "服务地址", 2048).rstrip("/")
    model = _text(data.get("model", current["model"]), "模型名称", 200)
    models = data.get("models", current.get("models", {}))
    if not isinstance(models, dict) or set(models) - TASKS:
        raise ValueError("用途模型设置无效。")
    models = {task: _text(value, "用途模型名称", 200) for task, value in models.items()}
    styles = data.get("styles", current.get("styles", {}))
    if not isinstance(styles, dict) or set(styles) - {"tone", "instructions"}:
        raise ValueError("创作风格设置无效。")
    styles = {"tone": _text(styles.get("tone", ""), "创作风格", 300),
              "instructions": _text(styles.get("instructions", ""), "写作要求", 8000)}
    parameters = data.get("parameters", current.get("parameters", {}))
    if not isinstance(parameters, dict) or set(parameters) - {"temperature", "max_tokens", "top_p", "presence_penalty", "frequency_penalty", "seed", "stop", "reasoning_effort", "thinking"}:
        raise ValueError("模型参数设置无效。")
    max_tokens = _integer(parameters.get("max_tokens", 3000), "输出上限", 1, 128_000)
    daily_limit = _integer(data.get("daily_limit", current.get("daily_limit", 0)), "每日生成上限", 0, 100_000)
    key = data.get("api_key")
    if key is not None and not isinstance(key, str):
        raise ValueError("密钥必须为文字。")
    if data.get("clear_api_key") is not None and not isinstance(data["clear_api_key"], bool):
        raise ValueError("清除密钥开关无效。")
    effective_key = key if key is not None else (
        _read_key(current.get("_credential_ref")) if original is not None else os.environ.get("MOYU_AI_API_KEY", ""))
    if data.get("clear_api_key"):
        effective_key = ""
    validation_config = {"base_url": base_url or "http://127.0.0.1",
                         "model": model or "settings-validation", "api_key": effective_key,
                         "parameters": {name: value for name, value in parameters.items() if name != "max_tokens"}}
    prepare_chat_request(validation_config, [{"role": "user", "content": "settings-validation"}],
                         max_tokens=max_tokens)
    next_payload = {
        "base_url": base_url, "model": model, "models": models, "styles": styles,
        "parameters": {**parameters, "max_tokens": max_tokens}, "daily_limit": daily_limit,
        "last_probe": current.get("last_probe"), "_credential_ref": current.get("_credential_ref"),
    }
    previous_public = {name: current.get(name) for name in ("base_url", "model", "models", "styles", "parameters")}
    if previous_public != {name: next_payload.get(name) for name in previous_public} or key is not None or data.get("clear_api_key"):
        next_payload["last_probe"] = None
    if key is not None or data.get("clear_api_key") or (original is None and effective_key):
        next_payload["_credential_ref"] = _store_key(conn, project, effective_key) if effective_key else None
    conn.execute(
        "INSERT INTO app_model_settings(project_id,settings_json,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(project_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at",
        (project, json.dumps(next_payload, ensure_ascii=False, allow_nan=False), _now()),
    )
    return public_settings(conn, project)


def probe_settings(conn: sqlite3.Connection, project: str,
                   *, cancel_event=None) -> dict[str, Any]:
    """Reserve a shared daily slot, release the write lock, then probe once."""
    config = provider_settings(conn, project)
    if not config["base_url"] or not config["model"]:
        raise ValueError("请先保存模型地址和名称。")
    messages = [{"role": "system", "content": "连接测试。只回复：连接正常"},
                {"role": "user", "content": "请回复连接正常。"}]
    run_id = "probe_" + uuid.uuid4().hex
    prepared = prepare_chat_request(config, messages, task="probe", max_tokens=32,
                                    parameters={"temperature": 0}, request_id=run_id)
    day = datetime.now(timezone.utc).date().isoformat()
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("UPDATE app_state_version SET version=version WHERE project_id=?", (project,))
    used = conn.execute(
        "SELECT COUNT(*) FROM ai_run a LEFT JOIN app_quota_reservation q ON q.ai_run_id=a.id "
        "WHERE a.project_id=? AND ((q.day=? AND q.status IN ('reserved','dispatched','consumed','unknown')) "
        "OR (q.ai_run_id IS NULL AND a.started_at LIKE ? AND a.status IN ('success','queued','running','unknown')))",
        (project, day, day + "%"),
    ).fetchone()[0]
    if config["daily_limit"] and used >= config["daily_limit"]:
        raise ValueError("已达到今日生成上限，连接测试未发送。")
    branch = conn.execute("SELECT id FROM branch WHERE project_id=? AND status='active' ORDER BY rowid LIMIT 1",
                          (project,)).fetchone()
    if branch is None:
        raise ValueError("作品没有可用的分支。")
    started_at = _now()
    initial_consent = {"task": "probe", "author_review_required": False,
                       "request_audit": prepared["request_audit"]}
    conn.execute(
        "INSERT INTO ai_run(id,project_id,branch_id,mode,provider,model,model_snapshot,"
        "system_prompt_hash,prompt_hash,input_hash,sampling_json,started_at,status,"
        "context_item_ids_json,token_json,cost_json,author_decision,consent_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, project, branch[0], "provider", "provider", config["model"], "",
         hashlib.sha256(messages[0]["content"].encode()).hexdigest(),
         prepared["request_audit"]["request_sha256"], prepared["request_audit"]["messages_sha256"],
         json.dumps(prepared["request_audit"]["effective_parameters"]), started_at, "running",
         "[]", "{}", '{"actual_cost":null,"cost_state":"unmeasured"}', "pending",
         json.dumps(initial_consent, ensure_ascii=False)),
    )
    conn.execute("INSERT INTO app_quota_reservation(ai_run_id,project_id,day,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                 (run_id, project, day, "reserved", started_at, started_at))
    conn.commit()
    probe: dict[str, Any] = {"checked_at": _now(), "success": False, "model": config["model"],
                             "requested_model": config["model"]}
    result: dict[str, Any] = {}
    status = "failed"
    try:
        result = call_chat_completion(config, messages, task="probe", max_tokens=32,
                                      parameters={"temperature": 0}, cancel_event=cancel_event,
                                      request_id=run_id)
        status = "success" if result["safe_to_apply"] else "unknown"
        probe.update({"success": result["safe_to_apply"], "returned_model": result["returned_model"],
                      "finish_reason": result["finish_reason"], "usage": result["usage"],
                      "billing_state": result["billing_state"], "latency_ms": result["latency_ms"]})
        probe["model_identity_match"] = (
            result["returned_model"] == config["model"] if result["returned_model"] else None)
        probe["model_identity_scope"] = "仅比较提供方回传名称，名称相同不能证明底层模型身份。"
    except ProviderError as exc:
        result = exc.partial_result
        status = exc.status
        probe.update({"error": str(exc), "error_code": exc.code,
                      "billing_state": exc.billing_state})
    finished_at = _now()
    billing = probe.get("billing_state", "unknown")
    quota_status = "released" if billing == "not_dispatched" else "consumed" if billing == "usage_reported" else "unknown"
    audit = result.get("request_audit") or prepared["request_audit"]
    conn.execute("UPDATE app_state_version SET version=version WHERE project_id=?", (project,))
    latest_saved = _saved(conn, project)
    latest_config = provider_settings(conn, project)
    probe["configuration_stale"] = latest_config != config
    accounting = usage_accounting(result.get("usage"), result.get("usage_accounting"))
    token_data = {"usage": result.get("usage"), "usage_state": accounting["state"],
                  "usage_accounting": accounting,
                  "requested_model": config["model"], "returned_model": result.get("returned_model")}
    initial_consent.update({"request_audit": audit, "billing_state": billing,
                            "completion_state": result.get("completion_state"), "probe": probe})
    conn.execute(
        "UPDATE ai_run SET status=?,model_snapshot=?,ended_at=?,error=?,token_json=?,latency_ms=?,consent_json=?,output_hash=? WHERE id=?",
        (status, result.get("returned_model") or "", finished_at, probe.get("error"),
         json.dumps(token_data, ensure_ascii=False), result.get("latency_ms"),
         json.dumps(initial_consent, ensure_ascii=False),
         hashlib.sha256(result.get("text", "").encode()).hexdigest() if result.get("text") else None, run_id),
    )
    conn.execute("UPDATE app_quota_reservation SET status=?,updated_at=? WHERE ai_run_id=?",
                 (quota_status, finished_at, run_id))
    if not probe["configuration_stale"]:
        payload = latest_saved if latest_saved is not None else _environment()
        payload["last_probe"] = probe
        if latest_saved is None and config["api_key"]:
            payload["_credential_ref"] = _store_key(conn, project, config["api_key"])
        conn.execute(
            "INSERT INTO app_model_settings(project_id,settings_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at",
            (project, json.dumps(payload, ensure_ascii=False, allow_nan=False), _now()),
        )
    return {"probe": probe, "ai_run_id": run_id, "settings": public_settings(conn, project)}
