"""Bounded, cancellable OpenAI-compatible chat-completions transport.

This module owns no database transaction. The caller must reserve its budget
before dispatch and persist each ``on_chunk`` callback in a short transaction.
There are no automatic retries: an interrupted dispatched request may be billed.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import socket
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit


MAX_REQUEST_BYTES = 2_000_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_LINE_BYTES = 256_000
MAX_JSON_DEPTH = 24
FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls", "function_call"}
_STRING = {"type": "string", "maxLength": 200_000}
_SHORT_STRING = {"type": "string", "maxLength": 12_000}
_STRINGS = {"type": "array", "items": _SHORT_STRING, "maxItems": 100}
_DIRECTION = {
    "type": "object",
    "required": ["name", "cost", "surprise"],
    "properties": {"name": _SHORT_STRING, "cost": _SHORT_STRING,
                   "surprise": _SHORT_STRING, "foreshadowing": _SHORT_STRING},
}
TASK_SCHEMAS: dict[str, dict[str, Any]] = {
    "continue": {"type": "object", "required": ["content"], "properties": {"content": _STRING}},
    "polish": {"type": "object", "required": ["content"], "properties": {"content": _STRING}},
    "outline": {
        "type": "object", "required": ["goal", "conflict", "turn", "scenes", "foreshadowing"],
        "properties": {"goal": _SHORT_STRING, "conflict": _SHORT_STRING, "turn": _SHORT_STRING,
                       "scenes": _STRINGS, "foreshadowing": _STRINGS},
    },
    "scene": {
        "type": "object", "required": ["pov", "location", "goal", "obstacle", "exit"],
        "properties": {key: _SHORT_STRING for key in
                       ("pov", "location", "goal", "obstacle", "exit", "time", "sensory", "actions")},
    },
    "summary": {
        "type": "object", "required": ["events", "state_changes", "open_threads"],
        "properties": {"events": _STRINGS, "state_changes": _STRINGS,
                       "open_threads": _STRINGS, "evidence": _STRING},
    },
    "check": {
        "type": "object", "required": ["findings", "evidence", "uncertainties"],
        "properties": {"findings": _STRINGS, "evidence": _STRINGS, "uncertainties": _STRINGS},
    },
    "brainstorm": {
        "type": "object", "required": ["directions"],
        "properties": {"directions": {"type": "array", "items": _DIRECTION,
                                      "minItems": 1, "maxItems": 20}},
    },
}
ChunkCallback = Callable[[dict[str, Any]], None]
DispatchCallback = Callable[[dict[str, Any]], None]


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _redact(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, "[密钥已隐藏]") if secret else value
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if isinstance(value, dict):
        return {_redact(key, secret): _redact(item, secret) for key, item in value.items()}
    return value


class ProviderError(ValueError):
    """Safe transport failure with any already-delivered candidate attached."""

    def __init__(self, message: str, *, code: str = "provider_error",
                 status: str = "failed", partial_result: dict[str, Any] | None = None,
                 request_dispatched: bool = False, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.partial_result = partial_result or {}
        self.request_dispatched = request_dispatched
        self.billing_state = "unknown" if request_dispatched else "not_dispatched"
        self.http_status = http_status

    def public(self) -> dict[str, Any]:
        return {"error": str(self), "error_code": self.code, "status": self.status,
                "request_dispatched": self.request_dispatched,
                "billing_state": self.billing_state, "http_status": self.http_status}


class ProviderCanceled(ProviderError):
    def __init__(self, *, partial_result: dict[str, Any] | None = None,
                 request_dispatched: bool = False) -> None:
        super().__init__("生成已停止；提供方是否计费尚未确认。"
                         if request_dispatched else "请求发送前已取消。",
                         code="canceled", status="canceled",
                         partial_result=partial_result,
                         request_dispatched=request_dispatched)


def _finite_number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProviderError(f"{label} 必须是有效数字。", code="invalid_request")
    if value < minimum or value > maximum:
        raise ProviderError(f"{label} 超出允许范围。", code="invalid_request")
    return value


def validate_json(value: Any, schema: Mapping[str, Any], path: str = "$",
                  depth: int = 0) -> None:
    """Validate the bounded JSON-schema subset used by author-facing tasks."""
    if depth > MAX_JSON_DEPTH:
        raise ProviderError("结构化回复嵌套过深。", code="invalid_structured_output")
    alternatives = schema.get("oneOf")
    if alternatives:
        matches = 0
        for option in alternatives:
            try:
                validate_json(value, option, path, depth + 1)
            except ProviderError:
                continue
            matches += 1
        if matches != 1:
            raise ProviderError(f"结构化回复 {path} 的类型不符合约定。",
                                code="invalid_structured_output")
        return
    type_name = schema.get("type")
    types = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool)
                              and math.isfinite(item),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if type_name and (type_name not in types or not types[type_name](value)):
        raise ProviderError(f"结构化回复 {path} 必须为 {type_name}。",
                            code="invalid_structured_output")
    if "enum" in schema and value not in schema["enum"]:
        raise ProviderError(f"结构化回复 {path} 的取值不符合约定。",
                            code="invalid_structured_output")
    if isinstance(value, dict):
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ProviderError(f"结构化回复 {path} 缺少字段：{', '.join(missing)}。",
                                code="invalid_structured_output")
        if len(value) > 1000:
            raise ProviderError("结构化回复字段过多。", code="invalid_structured_output")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key not in properties and schema.get("additionalProperties") is False:
                raise ProviderError(f"结构化回复 {path} 含有未允许的字段。",
                                    code="invalid_structured_output")
            child_schema = properties.get(key, schema.get("additionalProperties", {}))
            validate_json(item, child_schema if isinstance(child_schema, Mapping) else {},
                          f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000):
            raise ProviderError(f"结构化回复 {path} 的条目数量不符合约定。",
                                code="invalid_structured_output")
        for index, item in enumerate(value):
            validate_json(item, schema.get("items", {}), f"{path}[{index}]", depth + 1)
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 200_000):
            raise ProviderError(f"结构化回复 {path} 的文字长度不符合约定。",
                                code="invalid_structured_output")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ProviderError(f"结构化回复 {path} 的数字不符合约定。",
                                code="invalid_structured_output")
    elif value is not None and not isinstance(value, bool):
        raise ProviderError("结构化回复含有不支持的值。", code="invalid_structured_output")


def _load_json(raw: bytes | str, code: str = "invalid_response") -> Any:
    try:
        value = json.loads(raw, parse_constant=lambda _item: (_ for _ in ()).throw(ValueError()))
        validate_json(value, {})
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise ProviderError("提供方返回的 JSON 格式无效。", code=code) from None


def _endpoint(base_url: str, resource: str = "chat/completions") -> tuple[str, str, int, str]:
    if not isinstance(base_url, str) or len(base_url) > 2048 or any(ord(char) < 33 for char in base_url):
        raise ProviderError("模型服务地址无效。", code="invalid_configuration")
    try:
        parsed = urlsplit(base_url.rstrip("/"))
    except ValueError:
        raise ProviderError("模型服务地址无效。", code="invalid_configuration") from None
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError("模型服务地址必须为没有密码或查询参数的 HTTP/HTTPS 地址。",
                            code="invalid_configuration")
    if parsed.scheme == "http" and host.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ProviderError("远程模型服务必须使用 HTTPS。", code="invalid_configuration")
    try:
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise ProviderError("模型服务端口无效。", code="invalid_configuration") from None
    path = parsed.path.rstrip("/")
    # A copied full chat endpoint and an API base describe the same service.
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    path += "/" + resource
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")), host, port, path


def list_models(config: Mapping[str, Any]) -> dict[str, Any]:
    """Read bounded model identifiers once, without generating or following redirects."""
    endpoint, host, port, path = _endpoint(config.get("base_url", ""), "models")
    secret = config.get("api_key", "")
    if not isinstance(secret, str) or len(secret) > 4096 or any(
            ord(char) < 32 or ord(char) == 127 for char in secret):
        raise ProviderError("模型密钥格式无效。", code="invalid_configuration")
    timeout = _finite_number(config.get("connect_timeout", 10), "连接超时", 0.1, 90)
    total_timeout = _finite_number(config.get("total_timeout", 30), "总超时", 0.1, 90)
    headers = {"Accept": "application/json", "User-Agent": "Moyu/1"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    connection = (
        http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())
        if endpoint.startswith("https:") else http.client.HTTPConnection(host, port, timeout=timeout)
    )
    response = None
    started = time.monotonic()
    timed_out = threading.Event()

    def abort() -> None:
        timed_out.set()
        _abort_connection(connection, response)

    deadline = threading.Timer(total_timeout, abort)
    deadline.daemon = True
    deadline.start()
    try:
        connection.connect()
        if connection.sock:
            connection.sock.settimeout(max(0.1, total_timeout - (time.monotonic() - started)))
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if timed_out.is_set():
            raise ProviderError("获取模型列表超时，请稍后重试或手动填写模型名称。",
                                code="models_timeout")
        if response.status != 200:
            descriptions = {
                401: "密钥无效或已过期，请检查访问密钥。",
                403: "当前密钥没有访问权限，或被服务商拒绝。",
                404: "服务没有提供标准模型列表，可继续手动填写模型名称。",
                429: "服务暂时限流或账户额度不足，请稍后再试。",
            }
            message = descriptions.get(response.status, "请检查服务地址，或稍后再试。")
            raise ProviderError(f"获取模型列表失败（HTTP {response.status}）：{message}",
                                code="models_http_error", http_status=response.status)
        content_type = response.getheader("Content-Type", "").partition(";")[0].strip().lower()
        if content_type not in {"application/json", ""}:
            raise ProviderError("模型列表接口未返回 JSON，请检查服务地址，也可手动填写模型名称。",
                                code="invalid_models_response")
        parts: list[bytes] = []
        total = 0
        while True:
            remaining = total_timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise ProviderError("获取模型列表超时，请稍后重试或手动填写模型名称。",
                                    code="models_timeout")
            if connection.sock:
                connection.sock.settimeout(max(0.1, remaining))
            raw = response.read1(min(65_536, MAX_RESPONSE_BYTES - total + 1))
            if not raw:
                break
            total += len(raw)
            if total > MAX_RESPONSE_BYTES:
                raise ProviderError("模型列表超过大小上限，请手动填写模型名称。",
                                    code="models_too_large")
            parts.append(raw)
        if timed_out.is_set():
            raise ProviderError("获取模型列表超时，请稍后重试或手动填写模型名称。",
                                code="models_timeout")
        value = _load_json(b"".join(parts), "invalid_models_response")
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, list):
            raise ProviderError("服务返回的模型列表格式无效，可继续手动填写模型名称。",
                                code="invalid_models_response")
        models: list[str] = []
        for row in data:
            model = row.get("id") if isinstance(row, dict) else None
            if (not isinstance(model, str) or not model.strip() or len(model) > 200
                    or any(ord(char) < 32 or ord(char) == 127 for char in model)):
                raise ProviderError("模型列表包含无效名称，请手动填写模型名称。",
                                    code="invalid_models_response")
            safe = _redact(model, secret)
            if safe not in models:
                models.append(safe)
        return {"models": sorted(models, key=str.casefold), "endpoint": _redact(endpoint, secret)}
    except ProviderError:
        raise
    except (TimeoutError, socket.timeout):
        raise ProviderError("获取模型列表超时，请稍后重试或手动填写模型名称。",
                            code="models_timeout") from None
    except (OSError, http.client.HTTPException):
        if timed_out.is_set():
            raise ProviderError("获取模型列表超时，请稍后重试或手动填写模型名称。",
                                code="models_timeout") from None
        raise ProviderError("获取模型列表时连接中断，请检查服务地址或网络，也可手动填写模型名称。",
                            code="models_connection_interrupted") from None
    finally:
        deadline.cancel()
        connection.close()
        if response is not None:
            response.close()
        deadline.join(timeout=1)


def _returned_model(data: Mapping[str, Any], choice: Mapping[str, Any] | None = None) -> str | None:
    """Accept known gateway placements, never infer a model from a request ID."""
    observed = []
    for source in (data, choice or {}):
        if "model" not in source:
            continue
        value = source["model"]
        if (not isinstance(value, str) or not value.strip() or len(value) > 200
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ProviderError("提供方返回的模型名称无效。", code="invalid_response")
        observed.append(value)
    if len(set(observed)) > 1:
        raise ProviderError("同一回复中的模型名称发生改变。", code="invalid_response")
    return observed[0] if observed else None


def prepare_chat_request(
    config: Mapping[str, Any], messages: list[dict[str, str]], *,
    task: str = "continue", response_format: str = "text", max_tokens: int = 3000,
    stream: bool = False, parameters: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Return the exact safe payload and audit fields used for preview and send."""
    if not isinstance(config, Mapping) or response_format not in {"text", "json"}:
        raise ProviderError("模型请求设置无效。", code="invalid_request")
    endpoint, host, port, path = _endpoint(config.get("base_url", ""))
    task_models = config.get("task_models", {})
    if not isinstance(task_models, Mapping):
        raise ProviderError("用途模型设置无效。", code="invalid_configuration")
    model = task_models.get(task, config.get("model", ""))
    if not isinstance(model, str) or not model.strip() or len(model) > 200 or any(ord(char) < 32 for char in model):
        raise ProviderError("请配置有效的模型名称。", code="invalid_configuration")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
        raise ProviderError("模型输入消息无效。", code="invalid_request")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer", "user", "assistant"} or not isinstance(message.get("content"), str):
            raise ProviderError("模型消息必须含有角色和文字。", code="invalid_request")
        if set(message) - {"role", "content", "name"}:
            raise ProviderError("模型消息包含未允许的字段。", code="invalid_request")
        if "name" in message and (not isinstance(message["name"], str) or len(message["name"]) > 100):
            raise ProviderError("模型消息名称无效。", code="invalid_request")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 128_000:
        raise ProviderError("生成长度必须为 1 至 128000 的整数。", code="invalid_request")
    if not isinstance(stream, bool):
        raise ProviderError("分段回复开关无效。", code="invalid_request")
    defaults = config.get("parameters", {})
    by_task = config.get("task_parameters", {})
    if not isinstance(defaults, Mapping) or not isinstance(by_task, Mapping) or not isinstance(by_task.get(task, {}), Mapping):
        raise ProviderError("模型参数设置无效。", code="invalid_configuration")
    if parameters is not None and not isinstance(parameters, Mapping):
        raise ProviderError("模型参数必须为对象。", code="invalid_request")
    effective = {
        "temperature": config.get(
            "temperature", 0.65 if task in {"continue", "brainstorm", "scene"} else 0.35),
    }
    for layer in (defaults, by_task.get(task, {}), parameters or {}):
        # These are two representations of one setting. A more specific layer
        # replaces both so an inherited switch cannot contradict its new effort.
        if "thinking" in layer or "reasoning_effort" in layer:
            effective.pop("thinking", None)
            effective.pop("reasoning_effort", None)
        effective.update(layer)
    allowed = {"temperature", "top_p", "presence_penalty", "frequency_penalty", "seed",
               "stop", "reasoning_effort", "thinking"}
    unknown = set(effective) - allowed
    if unknown:
        raise ProviderError("模型参数包含未支持的字段。", code="invalid_request")
    for key, low, high in (("temperature", 0, 2), ("top_p", 0, 1),
                           ("presence_penalty", -2, 2), ("frequency_penalty", -2, 2)):
        if key in effective:
            _finite_number(effective[key], key, low, high)
    if "seed" in effective and (isinstance(effective["seed"], bool) or not isinstance(effective["seed"], int)
                                or abs(effective["seed"]) > 2**63 - 1):
        raise ProviderError("随机种子必须为有效整数。", code="invalid_request")
    if "stop" in effective:
        stop = effective["stop"]
        if isinstance(stop, str):
            stop = [stop]
        if not isinstance(stop, list) or not 1 <= len(stop) <= 4 or any(not isinstance(item, str) or not item or len(item) > 1000 for item in stop):
            raise ProviderError("停止词必须为至多四项非空文字。", code="invalid_request")
    if "reasoning_effort" in effective:
        effort = effective["reasoning_effort"]
        if not isinstance(effort, str) or effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ProviderError("思考强度设置无效。", code="invalid_request")
    if "thinking" in effective:
        thinking = effective["thinking"]
        if (not isinstance(thinking, Mapping) or set(thinking) != {"type"}
                or not isinstance(thinking["type"], str)
                or thinking["type"] not in {"enabled", "disabled"}):
            raise ProviderError("思考开关必须为仅含type的对象，值为enabled或disabled。", code="invalid_request")
        if "reasoning_effort" in effective and (
                (thinking["type"] == "disabled") != (effective["reasoning_effort"] == "none")):
            raise ProviderError("思考开关与思考强度冲突，请统一设置。", code="invalid_request")
        effective["thinking"] = {"type": thinking["type"]}
    token_parameter = config.get("output_token_parameter", "max_tokens")
    if token_parameter not in {"max_tokens", "max_completion_tokens"}:
        raise ProviderError("输出长度参数无效。", code="invalid_configuration")
    payload = {"model": model, "messages": messages, token_parameter: max_tokens,
               **effective, "stream": stream}
    if stream:
        include_usage = config.get("include_usage", False)
        if not isinstance(include_usage, bool):
            raise ProviderError("用量回报开关无效。", code="invalid_configuration")
        if include_usage:
            payload["stream_options"] = {"include_usage": True}
    if response_format == "json":
        payload["response_format"] = {"type": "json_object"}
    body = _encoded(payload)
    if len(body) > MAX_REQUEST_BYTES:
        raise ProviderError("模型输入超过请求大小上限。", code="invalid_request")
    secret = config.get("api_key", "")
    if not isinstance(secret, str) or len(secret) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in secret):
        raise ProviderError("模型密钥格式无效。", code="invalid_configuration")
    identifier = request_id or "moyu_" + uuid.uuid4().hex
    if not isinstance(identifier, str) or not identifier or len(identifier) > 128 or any(ord(char) < 33 or ord(char) > 126 for char in identifier):
        raise ProviderError("请求身份无效。", code="invalid_request")
    audit = {"request_id": identifier, "endpoint": endpoint, "method": "POST",
             "requested_model": model, "request_sha256": _sha(body),
             "messages_sha256": _sha(_encoded(messages)), "request_bytes": len(body),
             "effective_parameters": {**effective, token_parameter: max_tokens,
                                      "stream": stream, "response_format": response_format},
             "payload": _redact(payload, secret),
             "created_at": datetime.now(timezone.utc).isoformat(),
             "dispatched": False}
    return {"endpoint": endpoint, "host": host, "port": port, "path": path,
            "payload": payload, "body": body, "request_audit": _redact(audit, secret)}


def _token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def usage_accounting(usage: Any, observation: Any = None) -> dict[str, Any]:
    """Classify token evidence without changing the provider's raw counters."""
    counters = {}
    invalid = usage is not None and not isinstance(usage, dict)
    if isinstance(usage, dict):
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if name in usage:
                if _token_count(usage[name]):
                    counters[name] = usage[name]
                else:
                    invalid = True
        for name in ("prompt_tokens_details", "completion_tokens_details"):
            details = usage.get(name)
            if details is not None and (
                    not isinstance(details, dict)
                    or any(value is not None and not _token_count(value)
                           for value in details.values())):
                invalid = True
    prompt, completion, total = (
        counters.get(name) for name in ("prompt_tokens", "completion_tokens", "total_tokens"))
    lower_bound = max(total or 0, (prompt or 0) + (completion or 0))
    if total is not None:
        invalid = invalid or any(value > total for value in (prompt, completion) if value is not None)
        if prompt is not None and completion is not None:
            invalid = invalid or prompt + completion != total
    if invalid:
        state, confirmed_total, source = "inconsistent", None, "inconsistent_provider_counters"
    elif total is not None:
        state, confirmed_total, source = "reported", total, "provider_reported"
    elif prompt is not None and completion is not None:
        state, confirmed_total, source = "reported", prompt + completion, "derived_from_components"
    elif counters:
        state, confirmed_total, source = "partial", None, "partial_provider_counters"
    else:
        state, confirmed_total, source = "unknown", None, "unknown"
    if observation is not None:
        metadata_valid = isinstance(observation, dict)
        observed_state = observation.get("state") if metadata_valid else None
        observed_total = observation.get("total_tokens") if metadata_valid else None
        observed_lower = observation.get("reported_tokens_lower_bound") if metadata_valid else None
        observed_source = observation.get("source") if metadata_valid else None
        metadata_valid = metadata_valid and (
            isinstance(observed_state, str)
            and observed_state in {"unknown", "partial", "inconsistent", "reported"}
            and _token_count(observed_lower)
            and isinstance(observed_source, str)
            and "total_tokens" in observation
            and ((observed_state == "reported" and _token_count(observed_total)
                  and observed_lower == observed_total
                  and observed_source in {"provider_reported", "derived_from_components"})
                 or (observed_state != "reported" and observed_total is None
                     and observed_source == {
                         "unknown": "unknown", "partial": "partial_provider_counters",
                         "inconsistent": "inconsistent_provider_counters",
                     }.get(observed_state)
                     and (observed_state != "unknown" or observed_lower == 0))))
        if _token_count(observed_lower):
            lower_bound = max(lower_bound, observed_lower)
        if (not metadata_valid or state == "inconsistent" or observed_state == "inconsistent"
                or observed_state == "reported" and (
                    state != "reported" or observed_total != confirmed_total)
                or state == "reported" and lower_bound > confirmed_total):
            state, confirmed_total, source = "inconsistent", None, "inconsistent_provider_counters"
        elif observed_state != "reported" and state == "reported":
            state, confirmed_total, source = "partial", None, "partial_provider_counters"
        elif observed_state == "partial" or state == "unknown" and lower_bound:
            state, confirmed_total, source = "partial", None, "partial_provider_counters"
    return {"state": state, "total_tokens": confirmed_total,
            "reported_tokens_lower_bound": lower_bound, "source": source}


def _usage(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProviderError("提供方用量记录的类型无效。", code="invalid_response")
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] < 0):
            raise ProviderError("提供方用量记录必须为非负整数。", code="invalid_response")
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        details = value.get(key)
        if details is not None:
            if not isinstance(details, dict) or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in details.values() if item is not None):
                raise ProviderError("提供方详细用量记录无效。", code="invalid_response")
    return value


def _content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
                texts.append(item["text"])
            else:
                raise ProviderError("提供方返回了不支持的正文分片。", code="invalid_response")
        return "".join(texts)
    raise ProviderError("提供方正文必须为文字。", code="invalid_response")


def _value_shape(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, str):
        return {"type": "string", "chars": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "fields": len(value)}
    if isinstance(value, list):
        return {"type": "array", "items": len(value)}
    return {"type": type(value).__name__}


def _protocol_shape(data: Any, secret: str) -> dict[str, Any]:
    """Retain bounded protocol evidence without text or reasoning content."""
    result = _value_shape(data)
    if not isinstance(data, dict):
        return result
    result["keys"] = [_redact(str(key), secret)[:80] for key in list(data)[:32]]
    choices = data.get("choices")
    if isinstance(choices, list):
        result["choices"] = []
        for choice in choices[:2]:
            item = _value_shape(choice)
            if isinstance(choice, dict):
                finish = choice.get("finish_reason")
                item["finish_reason"] = (_redact(finish, secret)[:80]
                                         if isinstance(finish, str) else _value_shape(finish))
                for name in ("delta", "message"):
                    if name not in choice:
                        continue
                    message = choice[name]
                    item[name] = _value_shape(message)
                    if isinstance(message, dict):
                        item[name]["fields"] = {
                            _redact(str(key), secret)[:80]: _value_shape(value)
                            for key, value in list(message.items())[:32]
                        }
            result["choices"].append(item)
    if "usage" in data:
        result["usage"] = _value_shape(data["usage"])
    if "error" in data:
        result["error"] = _value_shape(data["error"])
    return result


def reasoning_field_counts(result: Mapping[str, Any]) -> tuple[int, int]:
    """Read content-free reasoning counts, falling back to older shape logs."""
    summary = result.get("reasoning_observation")
    legacy_zero = False
    if isinstance(summary, dict):
        nonempty = summary.get("nonempty_events")
        invalid = summary.get("invalid_events", 0)
        if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
               for value in (nonempty, invalid)):
            if "invalid_events" in summary or nonempty > 0:
                return nonempty, invalid
            legacy_zero = True
    observation = result.get("protocol_observation")
    events = observation.get("events") if isinstance(observation, dict) else None
    nonempty = invalid = 0
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        choices = event.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            for key in ("delta", "message"):
                message = choice.get(key)
                fields = message.get("fields") if isinstance(message, dict) else None
                if not isinstance(fields, dict):
                    continue
                for name in ("reasoning_content", "reasoning"):
                    if name not in fields:
                        continue
                    shape = fields[name]
                    if isinstance(shape, dict) and shape.get("type") == "null":
                        continue
                    if isinstance(shape, dict) and shape.get("type") == "string":
                        characters = shape.get("chars")
                        if (isinstance(characters, int) and not isinstance(characters, bool)
                                and characters >= 0):
                            # Old zero summaries counted only reasoning_content
                            # strings, including whitespace; alias fields were missed.
                            if not legacy_zero or name == "reasoning":
                                nonempty += characters > 0
                            continue
                    invalid += 1
    return nonempty, invalid


class _SecretStream:
    """Keep a potential secret prefix until it is safe to publish that chunk."""

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.pending = ""

    def feed(self, text: str, *, final: bool = False) -> str:
        if not self.secret:
            return text
        self.pending += text
        self.pending = self.pending.replace(self.secret, "[密钥已隐藏]")
        keep = 0
        if not final:
            for size in range(min(len(self.secret) - 1, len(self.pending)), 0, -1):
                if self.pending.endswith(self.secret[:size]):
                    keep = size
                    break
        ready = self.pending[:-keep] if keep else self.pending
        self.pending = self.pending[-keep:] if keep else ""
        return ready


class _Accumulator:
    def __init__(self, task: str, response_format: str, secret: str,
                 on_chunk: ChunkCallback | None, schema: Mapping[str, Any] | None,
                 *, thinking_disabled: bool = False) -> None:
        self.task = task
        self.response_format = response_format
        self.secret = secret
        self.on_chunk = on_chunk
        self.schema = schema or TASK_SCHEMAS.get(task, TASK_SCHEMAS["continue"])
        self.redactor = _SecretStream(secret)
        self.chunks: list[dict[str, Any]] = []
        self.text = ""
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None
        self.usage_lower_bound = 0
        self.usage_sequence_inconsistent = False
        self.returned_model: str | None = None
        self.provider_id = ""
        self.saw_done = False
        self.refused = False
        self.events_observed = 0
        self.protocol_events: list[dict[str, Any]] = []
        self.thinking_disabled = thinking_disabled
        self.reasoning_field_events = 0
        self.reasoning_nonempty_events = 0
        self.reasoning_invalid_events = 0
        self.reasoning_characters = 0

    def append(self, piece: str, *, event: str = "message", provider_id: str = "") -> None:
        if not piece:
            return
        self.text += piece
        chunk = {"seq": len(self.chunks) + 1, "text": piece,
                 "event": _redact(event, self.secret)[:80],
                 "provider_event_id": _redact(provider_id, self.secret)[:512],
                 "payload_hash": _sha(piece), "cumulative_hash": _sha(self.text)}
        self.chunks.append(chunk)
        if self.on_chunk:
            try:
                self.on_chunk(dict(chunk))
            except Exception:
                raise ProviderError("无法保存已生成分片，连接已停止。", code="chunk_persistence_failed") from None

    def feed(self, piece: str, *, event: str = "message", provider_id: str = "") -> None:
        self.append(self.redactor.feed(piece), event=event, provider_id=provider_id)

    def observe_usage(self, value: Any) -> None:
        incoming = usage_accounting(value)
        previous = self.usage or {}
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if (isinstance(value, dict) and _token_count(value.get(name))
                    and _token_count(previous.get(name)) and value[name] < previous[name]):
                self.usage_sequence_inconsistent = True
        self.usage_sequence_inconsistent |= incoming["state"] == "inconsistent"
        self.usage_lower_bound = max(
            self.usage_lower_bound, incoming["reported_tokens_lower_bound"])
        # Usage chunks are cumulative snapshots, not amounts to add together.
        # Preserve earlier fields if a later chunk reports only part of them.
        if isinstance(value, dict):
            merged = dict(previous)
            for name, item in value.items():
                if (name in {"prompt_tokens_details", "completion_tokens_details"}
                        and isinstance(item, dict) and isinstance(merged.get(name), dict)):
                    merged[name] = {**merged[name], **item}
                else:
                    merged[name] = item
            self.usage = _redact(merged, self.secret)
        try:
            _usage(value)
        except ProviderError:
            self.usage_sequence_inconsistent = True
            raise
        self.usage_lower_bound = max(
            self.usage_lower_bound, usage_accounting(self.usage)["reported_tokens_lower_bound"])

    def current_usage_accounting(self) -> dict[str, Any]:
        observed = usage_accounting(self.usage)
        if (self.usage_sequence_inconsistent
                or observed["total_tokens"] is not None
                and self.usage_lower_bound > observed["total_tokens"]):
            return usage_accounting(self.usage, {
                "state": "inconsistent", "total_tokens": None,
                "reported_tokens_lower_bound": self.usage_lower_bound,
                "source": "inconsistent_provider_counters",
            })
        observed["reported_tokens_lower_bound"] = max(
            observed["reported_tokens_lower_bound"], self.usage_lower_bound)
        return observed

    def payload(self, data: Any, *, sse: bool, event: str = "message", provider_id: str = "") -> None:
        self.events_observed += 1
        shape = {**_protocol_shape(data, self.secret), "event_sequence": self.events_observed}
        if len(self.protocol_events) == 128:
            del self.protocol_events[112]
        self.protocol_events.append(shape)
        if not isinstance(data, dict):
            raise ProviderError("提供方回复必须为对象。", code="invalid_response")
        if "usage" in data and data["usage"] is not None:
            self.observe_usage(data["usage"])
        if data.get("error") is not None or event == "error":
            raise ProviderError("提供方在生成途中返回错误。", code="provider_stream_error")
        def observe_model(choice=None):
            model = _returned_model(data, choice)
            if model is None:
                return
            model = _redact(model, self.secret)
            if self.returned_model is not None and self.returned_model != model:
                raise ProviderError("同一回复中的模型名称发生改变。", code="invalid_response")
            self.returned_model = model

        observe_model()
        if "id" in data:
            if not isinstance(data["id"], str) or len(data["id"]) > 512:
                raise ProviderError("提供方返回的请求身份无效。", code="invalid_response")
            self.provider_id = _redact(data["id"], self.secret)
        choices = data.get("choices")
        if not isinstance(choices, list):
            raise ProviderError("提供方回复缺少有效候选列表。", code="invalid_response")
        if not choices:
            if sse and (data.get("usage") is not None or data.get("moderation") is not None):
                return
            raise ProviderError("提供方没有返回候选。", code="invalid_response")
        if len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProviderError("提供方返回的候选数量或类型无效。", code="invalid_response")
        choice = choices[0]
        observe_model(choice)
        if "index" in choice and (isinstance(choice["index"], bool)
                                  or not isinstance(choice["index"], int)
                                  or choice["index"] != 0):
            raise ProviderError("提供方返回了错误候选序号。", code="invalid_response")
        finish = choice.get("finish_reason")
        # Some compatible gateways send an empty marker on unfinished SSE chunks.
        if sse and finish == "":
            finish = None
        if finish is not None:
            if not isinstance(finish, str) or finish not in FINISH_REASONS:
                raise ProviderError("提供方返回了未知结束原因。", code="invalid_response")
            if self.finish_reason is not None and self.finish_reason != finish:
                raise ProviderError("提供方重复返回了冲突的结束原因。", code="invalid_response")
            self.finish_reason = finish
        message_key = "delta" if sse else "message"
        message = choice.get(message_key)
        if message is None and isinstance(choice.get("text"), str):
            message = {"content": choice["text"]}
        if not isinstance(message, dict):
            raise ProviderError("提供方回复缺少有效正文对象。", code="invalid_response")
        # Counts cover every event; bounded shape logs can omit middle events.
        for name in ("reasoning_content", "reasoning"):
            if name not in message:
                continue
            self.reasoning_field_events += 1
            reasoning = message[name]
            if isinstance(reasoning, str):
                self.reasoning_characters += len(reasoning)
                self.reasoning_nonempty_events += bool(reasoning.strip())
            elif reasoning is not None:
                self.reasoning_invalid_events += 1
        if "role" in message and message["role"] != "assistant":
            raise ProviderError("提供方返回了错误正文角色。", code="invalid_response")
        refusal = message.get("refusal")
        if refusal is not None and not isinstance(refusal, str):
            raise ProviderError("提供方拒绝信息的类型无效。", code="invalid_response")
        self.refused = self.refused or bool(refusal)
        piece = _content(message.get("content", message.get("text")))
        if piece:
            self.feed(piece, event=event, provider_id=provider_id or self.provider_id)

    def result(self, *, sse: bool, state: str | None = None,
               finalize: bool = False) -> dict[str, Any]:
        if finalize:
            self.append(self.redactor.feed("", final=True), event="redaction-flush",
                        provider_id=self.provider_id)
        finish = self.finish_reason or "unknown"
        if state is None:
            state = {"stop": "complete", "length": "truncated",
                     "content_filter": "filtered", "tool_calls": "tool_call",
                     "function_call": "tool_call"}.get(finish, "unknown")
            if self.refused:
                state = "refused"
            if sse and not self.saw_done:
                state = "unknown"
        structured = None
        if self.response_format == "json" and self.text and state == "complete":
            structured = _load_json(self.text, "invalid_structured_output")
            validate_json(structured, self.schema)
            structured = _redact(structured, self.secret)
        accounting = self.current_usage_accounting()
        return {"text": self.text, "structured": structured, "response_format": self.response_format,
                "truncated": state != "complete", "finish_reason": finish,
                "completion_state": state, "safe_to_apply": state == "complete",
                "usage": self.usage, "usage_state": accounting["state"],
                "usage_accounting": accounting,
                "returned_model": self.returned_model, "provider_response_id": self.provider_id,
                "_chunks": [dict(chunk) for chunk in self.chunks],
                "stream_transport": "provider_native_sse" if sse else "provider_json",
                "provider_sse_complete": self.saw_done if sse else None,
                "reasoning_observation": {
                    "field_events": self.reasoning_field_events,
                    "nonempty_events": self.reasoning_nonempty_events,
                    "invalid_events": self.reasoning_invalid_events,
                    "characters": self.reasoning_characters,
                    "requested_disabled": self.thinking_disabled,
                    "unexpected_reasoning": self.thinking_disabled and (
                        self.reasoning_nonempty_events > 0 or self.reasoning_invalid_events > 0),
                },
                "protocol_observation": {
                    "events_observed": self.events_observed,
                    "events_recorded": len(self.protocol_events),
                    "events_truncated": self.events_observed > len(self.protocol_events),
                    "events": list(self.protocol_events),
                }}


def _read_sse(response: Any, accumulator: _Accumulator,
              account: Callable[[bytes], None], assert_running: Callable[[], None]) -> None:
    data_lines: list[str] = []
    event_name = "message"
    event_id = ""

    def dispatch() -> None:
        nonlocal data_lines, event_name, event_id
        if not data_lines:
            event_name, event_id = "message", ""
            return
        raw_event = "\n".join(data_lines)
        saved_name, saved_id = event_name, event_id
        data_lines, event_name, event_id = [], "message", ""
        if raw_event.strip() == "[DONE]":
            accumulator.saw_done = True
            return
        accumulator.payload(_load_json(raw_event), sse=True,
                            event=saved_name, provider_id=saved_id)

    while not accumulator.saw_done:
        assert_running()
        raw_line = response.readline(MAX_LINE_BYTES + 1)
        if not raw_line:
            break
        account(raw_line)
        if len(raw_line) > MAX_LINE_BYTES:
            raise ProviderError("模型回复单个分片过长。", code="response_too_large")
        try:
            line = raw_line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError:
            raise ProviderError("模型分片不是有效 UTF-8 文字。", code="invalid_response") from None
        if not line:
            dispatch()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
        elif field == "event":
            event_name = value or "message"
        elif field == "id" and "\x00" not in value:
            event_id = value


def normalize_json_completion(data: Any, task: str = "continue",
                              response_format: str = "text",
                              schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    accumulator = _Accumulator(task, response_format, "", None, schema)
    accumulator.payload(data, sse=False)
    return accumulator.result(sse=False, finalize=True)


def parse_sse_completion(response: Any, task: str = "continue",
                         response_format: str = "text", max_bytes: int = MAX_RESPONSE_BYTES,
                         schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compatibility parser for already-opened streams and offline fixtures."""
    accumulator = _Accumulator(task, response_format, "", None, schema)
    total = 0

    def account(raw: bytes) -> None:
        nonlocal total
        total += len(raw)
        if total > max_bytes:
            raise ProviderError("模型回复超过大小上限。", code="response_too_large")

    _read_sse(response, accumulator, account, lambda: None)
    return accumulator.result(sse=True, finalize=True)


def _abort_connection(connection: http.client.HTTPConnection,
                      response: http.client.HTTPResponse | None) -> None:
    sock = connection.sock
    if sock is None and response is not None:
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        connection.close()
    except OSError:
        pass


def call_chat_completion(
    config: Mapping[str, Any], messages: list[dict[str, str]], *,
    task: str = "continue", response_format: str = "text", max_tokens: int = 3000,
    stream: bool = False, parameters: Mapping[str, Any] | None = None,
    on_chunk: ChunkCallback | None = None, cancel_event: threading.Event | None = None,
    on_dispatch: DispatchCallback | None = None,
    request_id: str | None = None, schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Send once, emit safe chunks immediately, and close on cancel/deadline.

    Cancellation closes the client transport. It cannot guarantee that a remote
    provider has stopped its internal generation or its billing. No redirect is
    followed, preventing the Authorization header from reaching another host.
    """
    prepared = prepare_chat_request(config, messages, task=task,
                                    response_format=response_format, max_tokens=max_tokens,
                                    stream=stream, parameters=parameters, request_id=request_id)
    cancel = cancel_event or threading.Event()
    if cancel.is_set():
        raise ProviderCanceled()
    connect_timeout = _finite_number(config.get("connect_timeout", 10), "连接超时", 0.1, 90)
    idle_timeout = _finite_number(config.get("idle_timeout", 30), "等待分片超时", 0.1, 180)
    total_timeout = _finite_number(config.get("total_timeout", 180), "总超时", 0.1, 600)
    response_limit = config.get("max_response_bytes", MAX_RESPONSE_BYTES)
    if isinstance(response_limit, bool) or not isinstance(response_limit, int) or not 1 <= response_limit <= 10_000_000:
        raise ProviderError("模型回复大小上限无效。", code="invalid_configuration")
    secret = config.get("api_key", "")
    effective = prepared["payload"]
    thinking_disabled = (effective.get("thinking") == {"type": "disabled"}
                         or effective.get("reasoning_effort") == "none")
    accumulator = _Accumulator(task, response_format, secret, on_chunk, schema,
                               thinking_disabled=thinking_disabled)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json",
               "User-Agent": "Moyu/1", "X-Client-Request-Id": prepared["request_audit"]["request_id"]}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    if prepared["endpoint"].startswith("https:"):
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            prepared["host"], prepared["port"], timeout=connect_timeout, context=ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(prepared["host"], prepared["port"], timeout=connect_timeout)
    started = time.monotonic()
    state: dict[str, Any] = {"abort": None, "response": None, "last_activity": started,
                             "dispatched": False, "response_bytes": 0}
    finished = threading.Event()
    native_sse = False

    def watchdog() -> None:
        while not finished.wait(0.025):
            clock = time.monotonic()
            reason = "canceled" if cancel.is_set() else (
                "timeout" if clock - started >= total_timeout or (
                    native_sse and clock - state["last_activity"] >= idle_timeout) else None)
            if reason:
                state["abort"] = reason
                _abort_connection(connection, state["response"])
                return

    monitor = threading.Thread(target=watchdog, daemon=True, name="moyu-provider-watchdog")
    monitor.start()
    response: http.client.HTTPResponse | None = None

    def assert_running() -> None:
        if cancel.is_set() or state["abort"] == "canceled":
            raise ProviderCanceled(partial_result=accumulator.result(sse=native_sse, state="canceled"),
                                   request_dispatched=state["dispatched"])
        if state["abort"] == "timeout" or time.monotonic() - started >= total_timeout:
            raise ProviderError("模型回复超过等待时间；不会自动重复发送。",
                                code="timeout", status="unknown" if state["dispatched"] else "failed",
                                partial_result=accumulator.result(sse=native_sse, state="unknown"),
                                request_dispatched=state["dispatched"])

    def account(raw: bytes) -> None:
        state["response_bytes"] += len(raw)
        state["last_activity"] = time.monotonic()
        if state["response_bytes"] > response_limit:
            raise ProviderError("模型回复超过大小上限，连接已停止。", code="response_too_large")
        assert_running()

    def retain_failure(exc: ProviderError) -> ProviderError:
        exc.request_dispatched = state["dispatched"]
        if not exc.partial_result:
            exc.partial_result = accumulator.result(
                sse=native_sse, state="canceled" if isinstance(exc, ProviderCanceled) else "unknown")
        accounting = usage_accounting(
            exc.partial_result.get("usage"), exc.partial_result.get("usage_accounting"))
        exc.partial_result["usage_accounting"] = accounting
        exc.partial_result["usage_state"] = accounting["state"]
        exc.billing_state = ("usage_reported" if accounting["state"] == "reported"
                             else "unknown") if state["dispatched"] else "not_dispatched"
        prepared["request_audit"]["dispatched"] = state["dispatched"]
        if not state["dispatched"] and prepared["request_audit"].get("dispatch_state") == "dispatch_intent_recorded":
            prepared["request_audit"]["dispatch_state"] = "not_dispatched_after_intent"
        prepared["request_audit"]["response_bytes"] = state["response_bytes"]
        exc.partial_result.update({
            "request_audit": prepared["request_audit"],
            "requested_model": _redact(prepared["payload"]["model"], secret),
            "billing_state": exc.billing_state,
            "latency_ms": round((time.monotonic() - started) * 1000),
        })
        return exc

    try:
        connection.connect()
        assert_running()
        if connection.sock:
            connection.sock.settimeout(max(0.1, total_timeout - (time.monotonic() - started)))
        if on_dispatch:
            dispatch_audit = {**prepared["request_audit"], "dispatched": True,
                              "dispatch_state": "dispatch_intent_recorded"}
            try:
                on_dispatch(dispatch_audit)
            except Exception:
                raise ProviderError("无法保存发送前账本，模型请求未发送。",
                                    code="dispatch_audit_failed") from None
            prepared["request_audit"].update(dispatch_audit)
        assert_running()
        # Mark before send: a failed write may have delivered a billable prefix.
        state["dispatched"] = True
        prepared["request_audit"]["dispatched"] = True
        connection.request("POST", prepared["path"], body=prepared["body"], headers=headers)
        response = connection.getresponse()
        state["response"] = response
        state["last_activity"] = time.monotonic()
        assert_running()
        header_id = response.getheader("x-request-id", "")
        prepared["request_audit"]["provider_request_id"] = _redact(header_id, secret)[:512]
        if response.status != 200:
            raise ProviderError(f"模型服务返回 HTTP {response.status}。",
                                code="provider_http_error", http_status=response.status)
        content_type = (response.getheader("Content-Type", "").partition(";")[0]).strip().lower()
        native_sse = content_type == "text/event-stream"
        if native_sse:
            if connection.sock:
                connection.sock.settimeout(idle_timeout)
            _read_sse(response, accumulator, account, assert_running)
        else:
            if content_type not in {"application/json", ""}:
                raise ProviderError("模型服务返回了不支持的内容类型。", code="invalid_response")
            pieces: list[bytes] = []
            while True:
                assert_running()
                raw = response.read1(min(65_536, response_limit - state["response_bytes"] + 1))
                if not raw:
                    break
                account(raw)
                pieces.append(raw)
            accumulator.payload(_load_json(b"".join(pieces)), sse=False,
                                event="json-fallback" if stream else "json")
        assert_running()
        result = accumulator.result(sse=native_sse, finalize=True)
        if not result["text"].strip() and result["completion_state"] == "complete":
            raise ProviderError("模型没有返回正文。", code="empty_response")
        if stream and not native_sse:
            result["stream_transport"] = "provider_json_fallback"
        prepared["request_audit"]["response_bytes"] = state["response_bytes"]
        result["request_audit"] = prepared["request_audit"]
        result["requested_model"] = _redact(prepared["payload"]["model"], secret)
        result["billing_state"] = "usage_reported" if result["usage_state"] == "reported" else "unknown"
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        return result
    except ProviderError as exc:
        if not isinstance(exc, ProviderCanceled) and (cancel.is_set() or state["abort"] == "canceled"):
            exc = ProviderCanceled(partial_result=accumulator.result(sse=native_sse, state="canceled"),
                                   request_dispatched=state["dispatched"])
        raise retain_failure(exc) from None
    except KeyboardInterrupt as exc:
        retained = retain_failure(ProviderError(
            "人工中断，外部执行及费用需核实，禁止自动重发。",
            code="interrupted", status="unknown",
            partial_result=accumulator.result(sse=native_sse, state="unknown"),
            request_dispatched=state["dispatched"]))
        exc.partial_result = retained.partial_result
        raise
    except (OSError, http.client.HTTPException):
        try:
            assert_running()
        except ProviderError as exc:
            raise retain_failure(exc) from None
        raise retain_failure(ProviderError(
            "模型连接中断；不会自动重复可能已计费的请求。",
            code="connection_interrupted",
            status="unknown" if state["dispatched"] else "failed",
            partial_result=accumulator.result(sse=native_sse, state="unknown"),
            request_dispatched=state["dispatched"],
        )) from None
    finally:
        finished.set()
        _abort_connection(connection, response)
        if response is not None:
            response.close()
        monitor.join(timeout=1)
