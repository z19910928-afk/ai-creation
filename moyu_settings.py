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
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moyu_provider import (
    ProviderError, call_chat_completion, prepare_chat_request, reasoning_field_counts, usage_accounting,
    transport_options,
)


TASKS = {"continue", "polish", "outline", "scene", "summary", "check", "brainstorm",
         "chapter_analysis", "semantic_review"}
PARAMETERS = {"temperature", "max_tokens", "top_p", "presence_penalty",
              "frequency_penalty", "seed", "stop", "reasoning_effort", "thinking"}
PROBE_MAX_TOKENS = 1024
_REF = re.compile(r"^[a-f0-9]{64}-[a-f0-9]{32}$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _root() -> Path:
    configured = os.environ.get("MOYU_CREDENTIALS_DIR")
    directory = Path(configured).expanduser() if configured else Path.home() / ".moyu" / "credentials"
    if directory.is_symlink():
        raise ValueError("密钥目录不能为符号链接。")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
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
        if os.name == "posix":
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
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 20_000
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o600)):
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
            "model": os.environ.get("MOYU_AI_MODEL", "").strip(), "models": {}, "task_parameters": {},
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


def _connection_options(payload: dict[str, Any]) -> dict[str, Any]:
    api_protocol = payload.get("api_protocol", "chat_completions")
    if not isinstance(api_protocol, str) or api_protocol not in {"chat_completions", "responses"}:
        raise ValueError("模型接口协议无效。")
    token_parameter = payload.get("output_token_parameter", "max_tokens")
    if not isinstance(token_parameter, str) or token_parameter not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("输出长度参数无效。")
    include_usage = payload.get("include_usage", False)
    if not isinstance(include_usage, bool):
        raise ValueError("用量回报开关无效。")
    return {"api_protocol": api_protocol, "output_token_parameter": token_parameter,
            "include_usage": include_usage, **transport_options(payload)}


def _probe_response_valid(text: Any) -> bool:
    if not isinstance(text, str) or len(text) > 32:
        return False
    return text.strip().strip("\"'`“”‘’「」『』 \t\r\n。.!！?？") == "连接正常"


def _validate_saved_key_target(base_url: str, saved_base_url: str) -> None:
    from moyu_provider import _endpoint

    def service(value: str) -> tuple[str, str, int, str]:
        endpoint, host, port, path = _endpoint(value, "models")
        return urlsplit(endpoint).scheme, host.lower(), port, path

    if service(base_url) != service(saved_base_url):
        raise ValueError("服务地址已改变，请重新填写密钥；已保存的密钥不会发送到新地址。")


def _provider_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    parameters = dict(payload.get("parameters") or {})
    max_tokens = parameters.pop("max_tokens", 3000)
    models = payload.get("models") or {}
    task_models = {task: model for task, model in models.items() if model}
    if "summary" in task_models:
        task_models.setdefault("chapter_analysis", task_models["summary"])
    if "check" in task_models:
        task_models.setdefault("semantic_review", task_models["check"])
    task_parameters = {task: dict(values) for task, values in (payload.get("task_parameters") or {}).items()
                       if values}
    for source, target in (("summary", "chapter_analysis"), ("check", "semantic_review")):
        if source in task_parameters:
            task_parameters.setdefault(target, task_parameters[source])
    task_max_tokens = {task: values["max_tokens"] for task, values in task_parameters.items()
                       if "max_tokens" in values}
    sampling_by_task = {task: {key: value for key, value in values.items() if key != "max_tokens"}
                        for task, values in task_parameters.items()}
    return {"task_models": task_models, "parameters": parameters, "max_tokens": max_tokens,
            "task_parameters": sampling_by_task, "task_max_tokens": task_max_tokens,
            "styles": payload.get("styles") or {}, "daily_limit": payload.get("daily_limit", 0),
            **_connection_options(payload)}


def provider_settings(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    saved = _saved(conn, project)
    payload = saved if saved is not None else _environment()
    if payload.get("connection_workflow") is True:
        from moyu_model_connections import routing_config
        return routing_config(conn, project, payload)
    key = _read_key(payload.get("_credential_ref")) if saved is not None else os.environ.get("MOYU_AI_API_KEY", "")
    return {"base_url": payload.get("base_url", ""), "model": payload.get("model", ""),
            "api_key": key, **_provider_parameters(payload),
            "credential_ref": payload.get("_credential_ref"),
            "credential_fingerprint": hashlib.sha256(key.encode()).hexdigest() if key else None,
            "credential_source": "private_file" if saved is not None else "environment"}


def task_provider_settings(config: dict[str, Any], task: str) -> dict[str, Any]:
    """Resolve one verified connection before preparing or freezing a request."""
    if "_connection_routes" not in config:
        return config
    from moyu_model_connections import resolve_task_config
    return resolve_task_config(config, task)


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
    return {**snapshot, **_connection_options(snapshot), "api_key": key}


def public_settings(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    saved = _saved(conn, project)
    payload = saved if saved is not None else _environment()
    from moyu_model_connections import public_connections
    library = public_connections(conn, project)
    workflow = library["connection_workflow"]
    if workflow:
        selected = next((item for item in library["connections"]
                         if item["id"] == payload.get("default_connection_id")), None)
        base_url = selected["base_url"] if selected else ""
        model = selected["model"] if selected else ""
        configured = bool(selected and selected["verified"])
        key_configured = bool(selected and selected["api_key_configured"])
        probe = selected["last_probe"] if selected else None
    else:
        config = provider_settings(conn, project)
        base_url, model = config["base_url"], config["model"]
        configured = bool(base_url and model)
        key_configured = bool(config["api_key"])
        probe = _public_probe(payload.get("last_probe"))
    result = {
        "base_url": base_url, "model": model,
        "models": payload.get("models") or {}, "styles": payload.get("styles") or {},
        "parameters": payload.get("parameters") or {},
        "task_parameters": payload.get("task_parameters") or {},
        "daily_limit": payload.get("daily_limit", 0), "last_probe": probe,
        "configured": configured, "api_key_configured": key_configured,
        "api_key_masked": "已保存密钥" if key_configured else "",
        "credential_storage": "local_private_file",
        "credential_backup": "密钥不包含在作品备份中，换设备后需重新填写。",
        **library,
        "default_connection_id": payload.get("default_connection_id") or "",
        "task_connections": payload.get("task_connections") or {},
        **_connection_options(selected if workflow and selected else payload),
    }
    if workflow:
        result["models"] = {}
    return result


def _public_probe(probe: Any) -> dict[str, Any] | None:
    if not isinstance(probe, dict):
        return None
    public = dict(probe)
    identity_accepted = public.get("model_identity_match") is True or (
        public.get("identity_status") == "unreported"
        and public.get("model_identity_match") is None
        and not public.get("returned_model"))
    if public.get("model_identity_match") is True:
        public.setdefault("identity_status", "exact")
    elif public.get("model_identity_match") is False:
        public.setdefault("identity_status", "mismatch")
    if public.get("success") and not (
            public.get("protocol_complete") is True and public.get("response_valid") is True
            and identity_accepted and public.get("identity_status") != "mismatch"
            and public.get("reasoning_consistent") is True
            and public.get("configuration_stale") is not True):
        public.update(success=False, error_code="probe_legacy_unverified",
                      error="历史连接记录缺少完整回复验证，当前设置尚未按现行要求验证。")
    return public


def save_settings(conn: sqlite3.Connection, project: str, data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("设置必须为对象。")
    original = _saved(conn, project)
    assignments = {"default_connection_id", "task_connections"}
    workflow = bool(original and original.get("connection_workflow") is True) or bool(set(data) & assignments)
    if not workflow:
        return _save_legacy_settings(conn, project, data)
    if set(data) & {"base_url", "model", "api_key", "models", "clear_api_key", "api_protocol"}:
        raise ValueError("请先测试并保存模型连接，再从已验证的连接中分配用途。")
    allowed = assignments | {"styles", "parameters", "task_parameters", "daily_limit"}
    if set(data) - allowed:
        raise ValueError("设置包含未支持的字段。")
    from moyu_model_connections import validate_assignments
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("UPDATE app_state_version SET version=version WHERE project_id=?", (project,))
    savepoint = "model_assignments_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + savepoint)
    try:
        _save_legacy_settings(conn, project, {key: value for key, value in data.items()
                                            if key not in assignments},
                              return_public=False, validate_for_routing=True)
        payload = _saved(conn, project)
        payload.update(validate_assignments(conn, project, payload, data))
        payload["connection_workflow"] = True
        _write_settings(conn, project, payload)
        result = public_settings(conn, project)
        conn.execute("RELEASE SAVEPOINT " + savepoint)
        return result
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT " + savepoint)
        conn.execute("RELEASE SAVEPOINT " + savepoint)
        raise


def _write_settings(conn: sqlite3.Connection, project: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO app_model_settings(project_id,settings_json,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(project_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at",
        (project, json.dumps(payload, ensure_ascii=False, allow_nan=False), _now()),
    )


def _save_legacy_settings(conn: sqlite3.Connection, project: str, data: dict[str, Any],
                          *, return_public: bool = True,
                          validate_for_routing: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("设置必须为对象。")
    allowed = {"base_url", "model", "api_key", "models", "styles", "parameters", "task_parameters",
               "daily_limit", "clear_api_key", "output_token_parameter", "include_usage", "api_protocol",
               "provider", "custom_request_path", "models_path", "user_agent"}
    if set(data) - allowed:
        raise ValueError("设置包含未支持的字段。")
    original = _saved(conn, project)
    current = dict(original) if original is not None else _environment()
    base_url = _text(data.get("base_url", current["base_url"]), "服务地址", 2048).rstrip("/")
    model = _text(data.get("model", current["model"]), "模型名称", 200)
    connection_options = _connection_options({**current, **data})
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
    if not isinstance(parameters, dict) or set(parameters) - PARAMETERS:
        raise ValueError("模型参数设置无效。")
    max_tokens = _integer(parameters.get("max_tokens", 3000), "输出上限", 1, 128_000)
    task_parameters = data.get("task_parameters", current.get("task_parameters", {}))
    if not isinstance(task_parameters, dict) or set(task_parameters) - TASKS:
        raise ValueError("用途参数设置无效。")
    next_task_parameters = {}
    for task, overrides in task_parameters.items():
        if not isinstance(overrides, dict) or set(overrides) - PARAMETERS:
            raise ValueError("用途参数设置无效。")
        if "max_tokens" in overrides:
            _integer(overrides["max_tokens"], "用途输出上限", 1, 128_000)
        if overrides:
            next_task_parameters[task] = dict(overrides)
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
    # Assigned connections validate their own protocols after common parameter ranges.
    validation_options = ({**connection_options, "api_protocol": "chat_completions"}
                          if validate_for_routing else connection_options)
    validation_config = {"base_url": base_url or "http://127.0.0.1",
                         "model": model or "settings-validation", "api_key": effective_key,
                         "parameters": {name: value for name, value in parameters.items() if name != "max_tokens"},
                         **validation_options}
    prepare_chat_request(validation_config, [{"role": "user", "content": "settings-validation"}],
                         max_tokens=max_tokens)
    for task, overrides in next_task_parameters.items():
        prepare_chat_request(
            {**validation_config, "task_parameters": {
                task: {name: value for name, value in overrides.items() if name != "max_tokens"}}},
            [{"role": "user", "content": "settings-validation"}], task=task,
            max_tokens=overrides.get("max_tokens", max_tokens))
    next_payload = {**current,
        "base_url": base_url, "model": model, "models": models, "styles": styles,
        "parameters": {**parameters, "max_tokens": max_tokens}, "task_parameters": next_task_parameters,
        "daily_limit": daily_limit,
        "last_probe": current.get("last_probe"), "_credential_ref": current.get("_credential_ref"),
        **connection_options,
    }
    previous_public = {name: current.get(name) for name in (
        "base_url", "model", "models", "styles", "parameters", "task_parameters")}
    if (previous_public != {name: next_payload.get(name) for name in previous_public}
            or _connection_options(current) != connection_options
            or key is not None or data.get("clear_api_key")):
        next_payload["last_probe"] = None
    if key is not None or data.get("clear_api_key") or (original is None and effective_key):
        next_payload["_credential_ref"] = _store_key(conn, project, effective_key) if effective_key else None
    _write_settings(conn, project, next_payload)
    return public_settings(conn, project) if return_public else next_payload


def probe_settings(conn: sqlite3.Connection, project: str,
                   *, cancel_event=None, configuration_override=None, persist: bool = True,
                   configuration_guard=None) -> dict[str, Any]:
    """Reserve a shared daily slot, release the write lock, then probe once."""
    config = (dict(configuration_override) if configuration_override is not None
              else provider_settings(conn, project))
    if not config["base_url"] or not config["model"]:
        raise ValueError("请先保存模型地址和名称。")
    messages = [{"role": "system", "content": "连接测试。只回复：连接正常"},
                {"role": "user", "content": "请回复连接正常。"}]
    run_id = "probe_" + uuid.uuid4().hex
    prepared = prepare_chat_request(config, messages, task="probe", max_tokens=PROBE_MAX_TOKENS,
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
    probe: dict[str, Any] = {
        "checked_at": _now(), "success": False, "model": config["model"],
        "requested_model": config["model"], "protocol_complete": False,
        "response_valid": False, "reasoning_consistent": False,
    }
    result: dict[str, Any] = {}
    status = "failed"
    try:
        result = call_chat_completion(config, messages, task="probe", max_tokens=PROBE_MAX_TOKENS,
                                      parameters={"temperature": 0}, cancel_event=cancel_event,
                                      request_id=run_id)
        text = result.get("text", "")
        protocol_complete = (result.get("safe_to_apply") is True
                             and result.get("completion_state") == "complete"
                             and result.get("finish_reason") == "stop")
        observation = result.get("reasoning_observation") or {}
        nonempty_reasoning, invalid_reasoning = reasoning_field_counts(result)
        reasoning_consistent = not (
            observation.get("requested_disabled") is True
            and (nonempty_reasoning > 0 or invalid_reasoning > 0))
        response_valid = _probe_response_valid(text)
        probe.update({"protocol_complete": protocol_complete,
                      "response_valid": response_valid, "reasoning_consistent": reasoning_consistent,
                      "response_characters": len(text.strip()) if isinstance(text, str) else 0,
                      "returned_model": result.get("returned_model"),
                      "completion_state": result.get("completion_state"),
                      "finish_reason": result.get("finish_reason"), "usage": result.get("usage"),
                      "billing_state": result.get("billing_state"), "latency_ms": result.get("latency_ms")})
        probe["model_identity_match"] = (
            result.get("returned_model") == config["model"] if result.get("returned_model") else None)
        probe["identity_status"] = ("exact" if probe["model_identity_match"] is True
                                    else "mismatch" if probe["model_identity_match"] is False
                                    else "unreported")
        probe["model_identity_scope"] = "仅比较提供方回传名称，名称相同不能证明底层模型身份。"
        probe["success"] = bool(protocol_complete and response_valid and reasoning_consistent
                                and probe["model_identity_match"] is not False)
        if not protocol_complete:
            probe.update(error_code="probe_incomplete_response",
                         error="模型回复没有正常完整结束，连接检测未通过。")
        elif not response_valid:
            probe.update(error_code="probe_unexpected_response",
                         error=f"模型完整结束，但回复{probe['response_characters']}字，"
                               "未按连接测试要求只回复“连接正常”。")
        elif not reasoning_consistent:
            probe.update(error_code="probe_unexpected_reasoning",
                         error="已设置关闭思考，但思考字段类型无法校验，连接检测未通过。"
                         if invalid_reasoning > 0 else
                         "已设置关闭思考，但模型仍返回非空思考字段，连接检测未通过。")
        elif probe["model_identity_match"] is False:
            probe.update(error_code="probe_model_mismatch",
                         error="请求模型与回传名称不一致，当前所选模型尚未验证。")
        status = "success" if probe["success"] else "failed" if protocol_complete else "unknown"
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
    if configuration_guard is not None:
        probe["configuration_stale"] = not configuration_guard(conn)
    elif configuration_override is not None:
        probe["configuration_stale"] = False
    else:
        latest_config = provider_settings(conn, project)
        probe["configuration_stale"] = latest_config != config
    if probe["configuration_stale"]:
        probe.update(success=False, error_code="probe_configuration_stale",
                     error="检测期间设置发生变化，本次回复不能验证当前设置。")
        if status == "success":
            status = "failed"
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
    if persist and not probe["configuration_stale"]:
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
