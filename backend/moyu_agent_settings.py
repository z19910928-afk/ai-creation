"""Private, project-local connection settings for the writing agent chat."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from . import moyu_settings
from . import moyu_provider
from .moyu_provider import (
    ProviderError, call_chat_completion, prepare_chat_request, reasoning_field_counts,
    usage_accounting,
)

_OPTION_FIELDS = {"api_protocol", "output_token_parameter", "include_usage",
                  "provider", "custom_request_path", "models_path", "user_agent"}
_DRAFT_FIELDS = {"mode", "connection_id", "base_url", "model", "parameters",
                 "api_key", "clear_api_key"} | _OPTION_FIELDS


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def install_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_agent_connection(
          project_id TEXT PRIMARY KEY REFERENCES project(id),
          settings_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL
        )
    """)


def _default() -> dict[str, Any]:
    return {"mode": "inherit", "connection_id": "", "base_url": "", "model": "",
            "parameters": {"temperature": 0.35, "max_tokens": 3000},
            "_credential_ref": None, "last_probe": None,
            **moyu_settings._connection_options({})}


def _saved(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    row = conn.execute("SELECT settings_json FROM app_agent_connection WHERE project_id=?",
                       (project,)).fetchone()
    if row is None:
        return _default()
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        raise ValueError("写作总管连接设置损坏。") from None
    if not isinstance(payload, dict) or payload.get("mode", "inherit") not in {"inherit", "library", "independent"}:
        raise ValueError("写作总管连接设置损坏。")
    return {**_default(), **payload}


def _key(conn: sqlite3.Connection, project: str, payload: dict[str, Any]) -> str:
    reference = payload.get("_credential_ref")
    if reference and (not isinstance(reference, str)
                      or not reference.startswith(moyu_settings._project_prefix(conn, project) + "-")):
        raise ValueError("写作总管密钥来自其他作品或数据库，请重新填写。")
    return moyu_settings._read_key(reference)


def _write(conn: sqlite3.Connection, project: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO app_agent_connection(project_id,settings_json,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(project_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at",
        (project, json.dumps(payload, ensure_ascii=False, allow_nan=False), _now()),
    )


def _configuration(conn: sqlite3.Connection, project: str, saved: dict[str, Any]) -> dict[str, Any]:
    main = moyu_settings.provider_settings(conn, project)
    if saved["mode"] == "inherit":
        # Chat follows the selected default connection, never another writing purpose.
        return {**{key: value for key, value in main.items() if key != "_connection_routes"},
                "mode": "inherit", "connection_scope": "agent_chat"}
    if saved["mode"] == "library":
        from . import moyu_model_connections as library
        route = library._route(conn, project, library._payload(conn, project),
                               saved.get("connection_id") or "")
        # A library entry supplies its connection, not the main writer's sampling
        # or the last independent connection's protocol-specific parameters.
        config = {"base_url": "", "model": "", "api_key": "",
                  "parameters": {"temperature": 0.35}, "max_tokens": 3000,
                  "task_models": {}, "task_parameters": {}, "task_max_tokens": {},
                  "styles": main.get("styles") or {}, "daily_limit": main["daily_limit"],
                  "mode": "library", "connection_scope": "agent_chat",
                  "connection_id": saved.get("connection_id") or "", "connection_verified": False}
        if route and not route.get("_invalid"):
            config.update({name: value for name, value in route.items()
                           if name not in {"_invalid", "_verified_signature"}})
            config.update(api_key=library._private_key(conn, project, route.get("credential_ref")),
                          connection_verified=True)
        return config
    key = _key(conn, project, saved)
    parameters = dict(saved.get("parameters") or {})
    max_tokens = parameters.pop("max_tokens", 3000)
    return {"base_url": saved.get("base_url", ""), "model": saved.get("model", ""),
            "api_key": key, "parameters": parameters, "max_tokens": max_tokens,
            "task_models": {}, "task_parameters": {}, "task_max_tokens": {},
            "styles": main.get("styles") or {}, "daily_limit": main["daily_limit"],
            "credential_ref": saved.get("_credential_ref"),
            "credential_fingerprint": hashlib.sha256(key.encode()).hexdigest() if key else None,
            "credential_source": "private_file", "mode": "independent",
            "connection_scope": "agent_chat", **moyu_settings._connection_options(saved)}


def _independent_probe(saved: dict[str, Any]) -> tuple[Any, Any]:
    if saved.get("_independent_probe") is not None:
        return saved["_independent_probe"], saved.get("_independent_signature")
    return (saved.get("last_probe"), saved.get("_probe_signature")) if saved["mode"] == "independent" else (None, None)


def _independent_verified(saved: dict[str, Any], config: dict[str, Any]) -> bool:
    check, signature = _independent_probe(saved)
    return bool((moyu_settings._public_probe(check) or {}).get("success")
                and _signature_matches(signature, config))


def provider_settings(conn: sqlite3.Connection, project: str, *,
                      require_verified: bool = True) -> dict[str, Any]:
    saved = _saved(conn, project)
    config = _configuration(conn, project, saved)
    if require_verified:
        if saved["mode"] == "independent" and not _independent_verified(saved, config):
            raise ValueError("写作总管的独立连接尚未通过测试，请先测试并保存当前连接。")
        if saved["mode"] == "library" and not config.get("connection_verified"):
            raise ValueError("写作总管选择的模型已失效，请重新选择已验证模型。")
    return config


def _signature(config: dict[str, Any], *, legacy: bool = False,
               protocol_legacy: bool = False, transport_legacy: bool = False) -> str:
    fields = ("mode", "base_url", "model", "parameters", "max_tokens",
              "credential_fingerprint", "output_token_parameter")
    values = {name: config.get(name) for name in fields}
    if config.get("mode") == "library":
        values.update(connection_id=config.get("connection_id"),
                      connection_revision=config.get("connection_revision"))
    if legacy:
        values["output_token_parameter"] = None
    else:
        values.update(moyu_settings._connection_options(config))
        if transport_legacy:
            for name in moyu_provider.TRANSPORT_DEFAULTS:
                values.pop(name, None)
        if protocol_legacy:
            values.pop("api_protocol")
    encoded = json.dumps(values, sort_keys=True,
                         ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _signature_matches(signature: Any, config: dict[str, Any]) -> bool:
    if not isinstance(signature, str):
        return False
    if hmac.compare_digest(signature, _signature(config)):
        return True
    options = moyu_settings._connection_options(config)
    if any(options.get(name) != value for name, value in moyu_provider.TRANSPORT_DEFAULTS.items()):
        return False
    if hmac.compare_digest(signature, _signature(config, transport_legacy=True)):
        return True
    if options["api_protocol"] != "chat_completions":
        return False
    if hmac.compare_digest(signature, _signature(config, protocol_legacy=True, transport_legacy=True)):
        return True
    return (options == moyu_settings._connection_options({})
            and hmac.compare_digest(signature, _signature(config, legacy=True)))


def public_settings(conn: sqlite3.Connection, project: str) -> dict[str, Any]:
    saved = _saved(conn, project)
    config = _configuration(conn, project, saved)
    raw_probe, signature = (_independent_probe(saved) if saved["mode"] == "independent"
                            else (saved.get("last_probe"), saved.get("_probe_signature")))
    stale = bool(raw_probe and not _signature_matches(signature, config))
    probe = None if stale else moyu_settings._public_probe(raw_probe)
    parameters = {**config["parameters"], "max_tokens": config["max_tokens"]}
    independent_key = _key(conn, project, saved)
    configured = bool(config["base_url"] and config["model"]
                      and (saved["mode"] != "independent" or _independent_verified(saved, config))
                      and (saved["mode"] != "library" or config.get("connection_verified")))
    from . import moyu_model_connections as library
    return {
        "mode": saved["mode"], "connection_id": saved.get("connection_id") or "",
        "connection_scope": "agent_chat",
        "base_url": config["base_url"], "model": config["model"], "parameters": parameters,
        "configured": configured, "is_configured": configured,
        "api_key_configured": bool(config["api_key"]),
        "api_key_masked": "已保存密钥" if config["api_key"] else "",
        "daily_limit": config["daily_limit"], "last_probe": probe, "probe_stale": stale,
        "available_connections": library.public_connections(conn, project)["connections"],
        **moyu_settings._connection_options(config),
        "credential_storage": "local_private_file",
        "credential_backup": "密钥仅保存在这台电脑，作品备份中不包含密钥。",
        "independent_settings": {
            "base_url": saved["base_url"], "model": saved["model"],
            "parameters": saved["parameters"], "api_key_configured": bool(independent_key),
            "api_key_masked": "已保存密钥" if independent_key else "",
            **moyu_settings._connection_options(saved),
        },
    }


def _draft(conn: sqlite3.Connection, project: str, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("写作总管设置必须为对象。")
    if set(data) - _DRAFT_FIELDS:
        raise ValueError("写作总管设置包含未支持的字段。")
    current = _saved(conn, project)
    mode = data.get("mode", current["mode"])
    if not isinstance(mode, str) or mode not in {"inherit", "library", "independent"}:
        raise ValueError("请选择默认模型、已验证模型或独立连接。")
    if mode in {"inherit", "library"}:
        allowed = {"mode"} if mode == "inherit" else {"mode", "connection_id"}
        if set(data) - allowed:
            raise ValueError("使用已验证模型时无需重复填写独立连接。")
        next_payload = {**current, "mode": mode}
        if mode == "library":
            identifier = data.get("connection_id", current.get("connection_id"))
            if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
                raise ValueError("请选择已验证的模型。")
            next_payload["connection_id"] = identifier
        config = _configuration(conn, project, next_payload)
        if mode == "library" and not config.get("connection_verified"):
            raise ValueError("所选模型尚未通过验证，请先在模型库中测试并保存。")
        if mode == "library":
            prepare_chat_request(config, [{"role": "user", "content": "settings-validation"}],
                                 task="assistant_chat", response_format="json",
                                 max_tokens=config["max_tokens"], stream=True)
        return next_payload, config
    base_url = moyu_settings._text(data.get("base_url", current["base_url"]), "服务地址", 2048).rstrip("/")
    model = moyu_settings._text(data.get("model", current["model"]), "模型名称", 200)
    options = moyu_settings._connection_options({**current, **data})
    parameters = data.get("parameters", current["parameters"])
    if not isinstance(parameters, dict) or set(parameters) - moyu_settings.PARAMETERS:
        raise ValueError("写作总管模型参数设置无效。")
    max_tokens = moyu_settings._integer(parameters.get("max_tokens", 3000), "输出上限", 1, 128_000)
    parameters = {**parameters, "max_tokens": max_tokens}
    supplied_key = data.get("api_key")
    if supplied_key is not None and not isinstance(supplied_key, str):
        raise ValueError("密钥必须为文字。")
    clear = data.get("clear_api_key", False)
    if not isinstance(clear, bool):
        raise ValueError("清除密钥开关无效。")
    if clear and supplied_key:
        raise ValueError("请勿同时填写新密钥并清除密钥。")
    previous_key = _key(conn, project, current)
    key = "" if clear else supplied_key if supplied_key is not None else previous_key
    if key and supplied_key is None:
        moyu_settings._validate_saved_key_target(base_url, current["base_url"])
    if not base_url or not model:
        raise ValueError("请填写写作总管的服务地址和模型名称。")
    if not key and urlsplit(base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程连接需要填写密钥后测试。")
    prepare_chat_request(
        {"base_url": base_url, "model": model,
         "api_key": key, "parameters": {name: value for name, value in parameters.items()
                                      if name != "max_tokens"}, **options},
        [{"role": "user", "content": "settings-validation"}], task="agent_chat",
        max_tokens=max_tokens,
    )
    next_payload = {**current, "mode": mode, "base_url": base_url, "model": model,
                    "parameters": parameters, **options}
    config = _configuration(conn, project, {**next_payload, "_credential_ref": None})
    config.update(api_key=key, credential_fingerprint=hashlib.sha256(key.encode()).hexdigest() if key else None,
                  credential_ref=current.get("_credential_ref") if key == previous_key else None)
    return next_payload, config


def _active_stamp(conn: sqlite3.Connection, project: str) -> tuple[Any, str]:
    saved = _saved(conn, project)
    return saved.get("_revision"), _signature(_configuration(conn, project, saved))


def _pending(saved: dict[str, Any]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    result = []
    receipts = saved.get("_pending_probes")
    for receipt in receipts if isinstance(receipts, list) else []:
        try:
            if isinstance(receipt, dict) and datetime.fromisoformat(receipt["expires_at"]) > now:
                result.append(receipt)
        except (KeyError, TypeError, ValueError):
            continue
    return result[-12:]


def save_settings(conn: sqlite3.Connection, project: str, data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("写作总管设置必须为对象。")
    current = _saved(conn, project)
    if "verification_token" in data:
        if set(data) - {"verification_token", "mode"} or data.get("mode", "independent") != "independent":
            raise ValueError("保存测试结果时请勿修改连接，请重新测试。")
        token = data["verification_token"]
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise ValueError("请先测试独立连接，成功后再保存。")
        conn.execute("UPDATE app_state_version SET version=version WHERE project_id=?", (project,))
        digest = hashlib.sha256(token.encode()).hexdigest()
        pending = _pending(current)
        receipt = next((item for item in pending if isinstance(item.get("token_hash"), str)
                        and hmac.compare_digest(item["token_hash"], digest)), None)
        if receipt is None:
            raise ValueError("连接测试凭证已过期或已使用，请重新测试。")
        if receipt.get("project_prefix") != moyu_settings._project_prefix(conn, project):
            raise ValueError("连接测试凭证不属于当前作品。")
        if receipt.get("active_stamp") != list(_active_stamp(conn, project)):
            raise ValueError("已保存的连接在测试后发生变化，请重新测试。")
        next_payload = {**current, **receipt["settings"], "mode": "independent"}
        config = _configuration(conn, project, next_payload)
        if not _independent_verified(next_payload, config):
            raise ValueError("测试所用凭据或连接已变化，请重新测试。")
        next_payload.update(last_probe=next_payload["_independent_probe"],
                            _probe_signature=next_payload["_independent_signature"],
                            _pending_probes=[item for item in pending if item is not receipt],
                            _revision=uuid.uuid4().hex)
    else:
        next_payload, config = _draft(conn, project, data)
        if next_payload["mode"] == "independent" and not _independent_verified(current, config):
            raise ValueError("请先测试当前独立连接，成功后再保存。")
        if current["mode"] == "independent":
            independent_check, independent_signature = _independent_probe(current)
            next_payload.update(_independent_probe=independent_check,
                                _independent_signature=independent_signature)
        changed = _signature(config) != _signature(_configuration(conn, project, current))
        if changed:
            next_payload.update(last_probe=None, _probe_signature=None, _revision=uuid.uuid4().hex,
                                _pending_probes=[])
        if next_payload["mode"] == "independent":
            next_payload.update(last_probe=next_payload.get("_independent_probe"),
                                _probe_signature=next_payload.get("_independent_signature"))
    _write(conn, project, next_payload)
    return public_settings(conn, project)


def list_models(conn: sqlite3.Connection, project: str, data: dict[str, Any]) -> dict[str, Any]:
    """Read an independent draft's inventory without changing saved settings."""
    if not isinstance(data, dict) or set(data) - (
            {"base_url", "api_key", "clear_api_key"} | _OPTION_FIELDS):
        raise ValueError("总管模型列表查询包含未支持的字段。")
    saved = _saved(conn, project)
    base_url = moyu_settings._text(
        data.get("base_url", saved["base_url"]), "服务地址", 2048).rstrip("/")
    options = moyu_settings._connection_options({**saved, **data})
    supplied = data.get("api_key")
    if supplied is not None and not isinstance(supplied, str):
        raise ValueError("密钥必须为文字。")
    clear = data.get("clear_api_key", False)
    if not isinstance(clear, bool):
        raise ValueError("清除密钥开关无效。")
    if clear and supplied:
        raise ValueError("请勿同时填写新密钥并清除密钥。")
    key = "" if clear else supplied if supplied is not None else _key(conn, project, saved)
    if not base_url:
        raise ValueError("请先填写服务地址。")
    if key and supplied is None:
        moyu_settings._validate_saved_key_target(base_url, saved["base_url"])
    try:
        local = urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}
    except ValueError:
        raise ProviderError("模型服务地址无效。", code="invalid_configuration") from None
    if not key and not local:
        raise ValueError("远程连接需要填写密钥后获取模型。")
    result = moyu_provider.list_models({"base_url": base_url, "api_key": key, **options})
    return moyu_provider._redact(
        {"models": result["models"], "endpoint": result["endpoint"]}, key)


def probe_draft(conn: sqlite3.Connection, project: str, data: dict[str, Any],
                *, cancel_event=None) -> dict[str, Any]:
    payload, config = _draft(conn, project, data)
    if payload["mode"] != "independent":
        raise ValueError("默认模型和模型库连接无需另建独立连接。")
    stamp = _active_stamp(conn, project)
    result = probe(conn, project, cancel_event=cancel_event, configuration_override=config)
    if not result["probe"].get("success"):
        return result
    current = _saved(conn, project)
    if stamp != _active_stamp(conn, project):
        raise ValueError("检测期间已保存连接发生变化，请重新测试。")
    key = config["api_key"]
    reference = config.get("credential_ref")
    if key and reference is None:
        reference = moyu_settings._store_key(conn, project, key)
    verified = {name: payload[name] for name in
                ("base_url", "model", "parameters", *_OPTION_FIELDS)}
    verified.update(_credential_ref=reference, _independent_probe=result["probe"],
                    _independent_signature=_signature(config))
    token = secrets.token_urlsafe(32)
    receipt = {"token_hash": hashlib.sha256(token.encode()).hexdigest(),
               "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
               "project_prefix": moyu_settings._project_prefix(conn, project),
               "active_stamp": list(stamp), "settings": verified}
    _write(conn, project, {**current, "_pending_probes": (_pending(current) + [receipt])[-12:]})
    result.update(verification_token=token, settings=public_settings(conn, project))
    return result


def probe(conn: sqlite3.Connection, project: str, *, cancel_event=None,
          configuration_override=None) -> dict[str, Any]:
    """Use one shared daily slot and retain dispatch and protocol evidence."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("UPDATE app_state_version SET version=version WHERE project_id=?", (project,))
    saved_at_start = _saved(conn, project)
    config = (dict(configuration_override) if configuration_override is not None
              else provider_settings(conn, project, require_verified=False))
    active_signature = _signature(_configuration(conn, project, saved_at_start))
    signature = _signature(config)
    if not config["base_url"] or not config["model"]:
        raise ValueError("请先保存写作总管的模型地址和名称。")
    messages = [{"role": "system", "content": "连接测试。只回复：连接正常"},
                {"role": "user", "content": "请回复连接正常。"}]
    run_id = "agent_probe_" + uuid.uuid4().hex
    prepared = prepare_chat_request(config, messages, task="probe", max_tokens=moyu_settings.PROBE_MAX_TOKENS,
                                   parameters={"temperature": 0}, request_id=run_id)
    day = datetime.now(timezone.utc).date().isoformat()
    used = conn.execute(
        "SELECT COUNT(*) FROM ai_run a LEFT JOIN app_quota_reservation q ON q.ai_run_id=a.id "
        "WHERE a.project_id=? AND ((q.day=? AND q.status IN ('reserved','dispatched','consumed','unknown')) "
        "OR (q.ai_run_id IS NULL AND a.started_at LIKE ? AND a.status IN ('success','queued','running','unknown')))",
        (project, day, day + "%"),
    ).fetchone()[0]
    if config["daily_limit"] and used >= config["daily_limit"]:
        raise ValueError("已达到今日共享生成上限，连接测试未发送。")
    branch = conn.execute("SELECT id FROM branch WHERE project_id=? AND status='active' ORDER BY rowid LIMIT 1",
                          (project,)).fetchone()
    if branch is None:
        raise ValueError("作品没有可用的分支。")
    started_at = _now()
    consent = {"task": "probe", "connection_scope": "agent_chat", "mode": config["mode"],
               "author_review_required": False, "request_audit": prepared["request_audit"]}
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
         json.dumps(consent, ensure_ascii=False)),
    )
    conn.execute(
        "INSERT INTO app_quota_reservation(ai_run_id,project_id,day,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (run_id, project, day, "reserved", started_at, started_at),
    )
    conn.commit()
    dispatch_recorded = False

    def on_dispatch(audit: dict[str, Any]) -> None:
        nonlocal dispatch_recorded
        conn.execute("BEGIN IMMEDIATE")
        latest = _saved(conn, project)
        if (latest.get("_revision") != saved_at_start.get("_revision")
                or _signature(_configuration(conn, project, latest)) != active_signature):
            conn.rollback()
            raise ValueError("检测发送前连接设置发生变化。")
        consent["request_audit"] = audit
        conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?",
                     (json.dumps(consent, ensure_ascii=False), run_id))
        conn.execute("UPDATE app_quota_reservation SET status='dispatched',updated_at=? WHERE ai_run_id=?",
                     (_now(), run_id))
        conn.commit()
        dispatch_recorded = True

    check: dict[str, Any] = {
        "checked_at": _now(), "success": False, "model": config["model"],
        "requested_model": config["model"], "protocol_complete": False,
        "response_valid": False, "reasoning_consistent": False,
    }
    result: dict[str, Any] = {}
    status = "failed"
    try:
        result = call_chat_completion(
            config, messages, task="probe", max_tokens=moyu_settings.PROBE_MAX_TOKENS,
            parameters={"temperature": 0},
            cancel_event=cancel_event, request_id=run_id, on_dispatch=on_dispatch,
        )
        text = result.get("text", "")
        complete = (result.get("safe_to_apply") is True
                    and result.get("completion_state") == "complete"
                    and result.get("finish_reason") == "stop")
        nonempty_reasoning, invalid_reasoning = reasoning_field_counts(result)
        consistent = not ((result.get("reasoning_observation") or {}).get("requested_disabled") is True
                          and (nonempty_reasoning > 0 or invalid_reasoning > 0))
        valid = moyu_settings._probe_response_valid(text)
        match = result.get("returned_model") == config["model"] if result.get("returned_model") else None
        check.update({
            "protocol_complete": complete, "response_valid": valid, "reasoning_consistent": consistent,
            "response_characters": len(text.strip()) if isinstance(text, str) else 0,
            "returned_model": result.get("returned_model"), "model_identity_match": match,
            "identity_status": "exact" if match is True else "mismatch" if match is False else "unreported",
            "model_identity_scope": "仅比较提供方回传名称，名称相同不能证明底层模型身份。",
            "completion_state": result.get("completion_state"), "finish_reason": result.get("finish_reason"),
            "usage": result.get("usage"), "billing_state": result.get("billing_state"),
            "latency_ms": result.get("latency_ms"),
            "success": bool(complete and valid and consistent and match is not False),
        })
        if not complete:
            check.update(error_code="probe_incomplete_response", error="模型回复没有正常完整结束，连接检测未通过。")
        elif not valid:
            check.update(error_code="probe_unexpected_response",
                         error=f"模型完整结束，但回复{check['response_characters']}字，未按要求只回复“连接正常”。")
        elif not consistent:
            check.update(error_code="probe_unexpected_reasoning",
                         error="已设置关闭思考，但思考字段类型无法校验，连接检测未通过。"
                         if invalid_reasoning else "已设置关闭思考，但模型仍返回非空思考字段，连接检测未通过。")
        elif match is False:
            check.update(error_code="probe_model_mismatch",
                         error="请求模型与回传名称不一致，当前所选模型尚未验证。")
        status = "success" if check["success"] else "failed" if complete else "unknown"
    except ProviderError as exc:
        result = exc.partial_result
        status = exc.status
        check.update(error=str(exc), error_code=exc.code, billing_state=exc.billing_state)
    except Exception:
        conn.rollback()
        status = "unknown" if dispatch_recorded else "failed"
        check.update(error="连接检测中断，已保留调用记录；不会自动重复发送。",
                     error_code="probe_interrupted",
                     billing_state="unknown" if dispatch_recorded else "not_dispatched")
    finished_at = _now()
    billing = check.get("billing_state", "unknown")
    quota_status = "released" if billing == "not_dispatched" else "consumed" if billing == "usage_reported" else "unknown"
    conn.execute("UPDATE app_state_version SET version=version WHERE project_id=?", (project,))
    latest_saved = _saved(conn, project)
    latest_config = _configuration(conn, project, latest_saved)
    check["configuration_stale"] = (
        latest_saved.get("_revision") != saved_at_start.get("_revision")
        or _signature(latest_config) != active_signature
    )
    if check["configuration_stale"]:
        check.update(success=False, error_code="probe_configuration_stale",
                     error="检测期间设置发生变化，本次回复不能验证当前设置。")
        if status == "success":
            status = "failed"
    accounting = usage_accounting(result.get("usage"), result.get("usage_accounting"))
    token_data = {"usage": result.get("usage"), "usage_state": accounting["state"],
                  "usage_accounting": accounting, "requested_model": config["model"],
                  "returned_model": result.get("returned_model")}
    consent.update(request_audit=result.get("request_audit") or consent["request_audit"],
                   billing_state=billing, completion_state=result.get("completion_state"), probe=check)
    conn.execute(
        "UPDATE ai_run SET status=?,model_snapshot=?,ended_at=?,error=?,token_json=?,latency_ms=?,"
        "consent_json=?,output_hash=? WHERE id=?",
        (status, result.get("returned_model") or "", finished_at, check.get("error"),
         json.dumps(token_data, ensure_ascii=False), result.get("latency_ms"),
         json.dumps(consent, ensure_ascii=False),
         hashlib.sha256(result.get("text", "").encode()).hexdigest() if result.get("text") else None, run_id),
    )
    conn.execute("UPDATE app_quota_reservation SET status=?,updated_at=? WHERE ai_run_id=?",
                 (quota_status, finished_at, run_id))
    if configuration_override is None and not check["configuration_stale"]:
        update = {**latest_saved, "last_probe": check, "_probe_signature": signature}
        if latest_saved["mode"] == "independent":
            update.update(_independent_probe=check, _independent_signature=signature)
        _write(conn, project, update)
    return {"probe": check, "ai_run_id": run_id, "settings": public_settings(conn, project)}
