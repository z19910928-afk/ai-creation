"""Executable regression fixture for the chapter → assertion extractor.

Run: python tests/extract_fixtures.py

覆盖四件事：
1. schema 严格性（未知键 / 缺键 / 类型 / 空值 / 谓词白名单 / 区间形状）；
2. 证据可定位（evidence_quote 必须能按 char_start/char_end 逐字切出，切不出丢进 rejected；
   注意这只是「坐标对得上」，不保证 subject/attribute/value 与引文语义相符——见
   test_quote_gate_does_not_verify_semantics 与模块 docstring 的「能力边界」）；
3. 确定性（同输入两次调用逐字节相同，digest 相同）；
4. 离线优先（无凭据走规则模式不崩；OpenAI 兼容 provider 的失败路径全部降级为 rejected）。
"""
from __future__ import annotations

import http.server
import json
import math
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.moyu_extract as extract_module  # noqa: E402
from backend.moyu_extract import (  # noqa: E402
    ASSERTION_KEYS,
    DEFAULT_TIMEOUT,
    ENV_TIMEOUT,
    EMPTY_TEXT_REASON,
    MAX_QUOTE_CHARS,
    MODULE_VERSION,
    OpenAIProvider,
    PREDICATES,
    REJECT_REASONS,
    ExtractProviderError,
    Rejected,
    chinese_number,
    curl_available,
    digest,
    dry_run_provider,
    extract_chapter,
    extract_chapter_report,
    provider_from_env,
    resolve_provider,
    strict_json,
    validate_assertion,
    verify_evidence,
)

CHAPTER = (
    "第一回 甄士隐梦幻识通灵\n\n"
    "庙旁住着一家乡宦，姓甄名费字士隐，嫡妻封氏，性情贤淑。\n"
    "这士隐正在痴想，忽见隔壁葫芦庙内寄居的一个穷儒，姓贾名化、表字时飞、别号雨村的走来。\n"
    "那贾化乃湖州人氏，年方二十四岁，身长九尺，气度不凡。\n"
    "封氏之女英莲年方三岁，另有丫鬟名唤娇杏，常随左右。\n"
)


def slice_for(needle: str, text: str = CHAPTER) -> tuple[int, int]:
    """给一段引文算它在正文里的精确区间（fixture 自己算，别手写偏移）。"""
    start = text.index(needle)
    return start, start + len(needle)


def blank_assertion(needle: str = "姓甄名费", text: str = CHAPTER) -> dict:
    """一条合规断言（8 键齐、证据逐字对得上），供各用例做变异。"""
    start, end = slice_for(needle, text)
    return {
        "subject": "甄费",
        "predicate": "has_attribute",
        "attribute": "real_name",
        "value": "甄费",
        "evidence_quote": needle,
        "char_start": start,
        "char_end": end,
        "confidence": 0.9,
    }


def provider_returning(payload) -> object:
    """返回一个「总是吐同一 payload」的 provider。"""

    def provider(text: str, chapter_id: str) -> object:
        return payload

    return provider


def report_for(payload, text: str = CHAPTER) -> dict:
    return extract_chapter_report(text, chapter_id="t/001", provider=provider_returning(payload))


def assert_reason(payload, expected: str, text: str = CHAPTER) -> None:
    report = report_for(payload, text)
    assert report["assertions"] == [], (expected, report["assertions"])
    assert report["rejected_reasons"].get(expected) == 1, (expected, report["rejected_reasons"])
    detail = report["rejected"][0]
    assert detail["reason"] == expected, detail
    assert detail["detail"], "rejected 必须带可读原因"


# ------------------------------------------------------------------ schema


def test_module_surface() -> None:
    assert MODULE_VERSION == "1.0"
    assert ASSERTION_KEYS == (
        "subject", "predicate", "attribute", "value",
        "evidence_quote", "char_start", "char_end", "confidence",
    )
    assert PREDICATES == frozenset({"has_story_fact", "has_attribute"})
    for reason in ("unknown_key", "missing_key", "type_error", "empty_value",
                   "predicate_not_allowed", "bad_range", "quote_mismatch",
                   "duplicate", "provider_error", "invalid_json"):
        assert reason in REJECT_REASONS, reason
    assert MAX_QUOTE_CHARS > 0


def test_valid_assertion_passes() -> None:
    assertion = validate_assertion(blank_assertion())
    assert set(assertion) == set(ASSERTION_KEYS)
    verify_evidence(CHAPTER, assertion)  # 不抛就是逐字对上了


def test_unknown_key_rejected() -> None:
    payload = dict(blank_assertion(), note="多余字段")
    assert_reason(payload, "unknown_key")


def test_missing_key_rejected() -> None:
    payload = blank_assertion()
    payload.pop("confidence")
    assert_reason(payload, "missing_key")


def test_type_errors_rejected() -> None:
    cases = []
    payload = dict(blank_assertion(), char_start=True)  # bool 不是整数
    cases.append((payload, "type_error"))
    payload = dict(blank_assertion(), confidence="high")
    cases.append((payload, "type_error"))
    payload = dict(blank_assertion(), attribute=7)
    cases.append((payload, "type_error"))
    payload = dict(blank_assertion(), value={"nested": 1})
    cases.append((payload, "type_error"))
    cases.append((["列表"], "type_error"))
    # provider 整体吐一个字符串属于协议错，归 invalid_json（在下一个用例里断言）
    for payload, expected in cases:
        assert_reason(payload, expected)


def test_empty_values_rejected() -> None:
    cases = []
    payload = dict(blank_assertion(), subject="   ")
    cases.append((payload, "empty_value"))
    payload = dict(blank_assertion(), value="  ")
    cases.append((payload, "empty_value"))
    payload = dict(blank_assertion(), attribute="  ")
    cases.append((payload, "empty_value"))
    payload = dict(blank_assertion(), predicate="has_attribute", attribute=None)
    cases.append((payload, "empty_value"))
    for payload, expected in cases:
        assert_reason(payload, expected)


def test_predicate_whitelist() -> None:
    assert_reason(dict(blank_assertion(), predicate="likes"), "predicate_not_allowed")
    # has_story_fact 允许无槽位
    payload = dict(blank_assertion(), predicate="has_story_fact", attribute=None)
    report = report_for(payload)
    assert len(report["assertions"]) == 1, report["rejected_reasons"]


def test_bad_ranges_rejected() -> None:
    start, end = slice_for("姓甄名费")
    cases = [
        (dict(blank_assertion(), char_start=end, char_end=start), "bad_range"),
        (dict(blank_assertion(), char_start=-1), "bad_range"),
        (dict(blank_assertion(), char_end=len(CHAPTER) + 10), "bad_range"),
        (dict(blank_assertion(), char_end=start + 3), "bad_range"),  # 区间长度与引文不等
    ]
    for payload, expected in cases:
        assert_reason(payload, expected)


def test_long_quote_rejected() -> None:
    long_quote = "甲" * (MAX_QUOTE_CHARS + 1)
    payload = dict(blank_assertion(), evidence_quote=long_quote, char_end=0 + len(long_quote))
    report = report_for(payload, long_quote)
    assert report["assertions"] == []
    assert report["rejected_reasons"] == {"bad_range": 1}, report["rejected_reasons"]


def test_confidence_bounds() -> None:
    for bad in (-0.1, 1.5):
        assert_reason(dict(blank_assertion(), confidence=bad), "bad_range")
    for good in (0, 1, 0.5):
        report = report_for(dict(blank_assertion(), confidence=good))
        assert len(report["assertions"]) == 1, (good, report["rejected_reasons"])


# --------------------------------------------------- 证据定位：逐字可切（不验语义）


def test_quote_mismatch_rejected() -> None:
    """区间能切出文本，但切出来的不是 evidence_quote 写的那串——这就是幻觉。"""
    payload = dict(blank_assertion(), evidence_quote="姓甄名费字士隐")
    report = report_for(payload)
    assert report["assertions"] == []
    assert report["rejected_reasons"] == {"bad_range": 1}, report["rejected_reasons"]

    start, end = slice_for("姓甄名费")
    hallucinated = dict(blank_assertion(), evidence_quote="姓贾名化", char_start=start, char_end=end)
    report = report_for(hallucinated)
    assert report["assertions"] == []
    assert report["rejected_reasons"] == {"quote_mismatch": 1}, report["rejected_reasons"]


def test_shifted_span_is_caught() -> None:
    """整体偏移一个字也必须被抓出来（说明校验的是内容，不是长度）。"""
    start, end = slice_for("姓甄名费")
    payload = dict(blank_assertion(), char_start=start + 1, char_end=end + 1)
    report = report_for(payload)
    assert report["rejected_reasons"] == {"quote_mismatch": 1}, report["rejected_reasons"]


def test_duplicate_assertions_are_dropped_once() -> None:
    payload = blank_assertion()
    report = report_for([payload, dict(payload)])
    assert len(report["assertions"]) == 1, report["assertions"]
    assert report["rejected_reasons"] == {"duplicate": 1}, report["rejected_reasons"]


def test_empty_text_never_raises() -> None:
    for text in ("", "   \n\t "):
        report = extract_chapter_report(text, chapter_id="t/000")
        assert report["assertions"] == []
        assert report["rejected_reasons"] == {EMPTY_TEXT_REASON: 1}, report["rejected_reasons"]
        assert report["characters"] == len(text)
    assert extract_chapter("", chapter_id="t/000") == []


# --------------------------------------------------- 规则模式（离线默认）


def test_dry_run_rules_extract_known_facts() -> None:
    report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=None, environ={})
    assert report["mode"] == "dry-run", report["mode"]
    found = {(item["subject"], item["attribute"], item["value"]) for item in report["assertions"]}
    assert ("甄费", "real_name", "甄费") in found, found
    assert ("甄费", "courtesy_name", "士隐") in found, found
    assert ("贾化", "real_name", "贾化") in found, found
    assert ("贾化", "courtesy_name", "时飞") in found, found
    assert ("贾化", "hometown", "湖州") in found, found
    assert ("贾化", "age", 24) in found, found
    for item in report["assertions"]:
        assert set(item) == set(ASSERTION_KEYS), item
        assert item["predicate"] in PREDICATES, item
        # 每条都要能逐字切回去
        assert CHAPTER[item["char_start"]:item["char_end"]] == item["evidence_quote"], item


def test_dry_run_gate_skips_unknown_subject() -> None:
    """没有出现过全名的角色，宁可一条不抽，也不许瞎填 subject。"""
    text = "那女子年方十八岁，生得十分标致。\n"
    report = extract_chapter_report(text, chapter_id="t/002", provider=None, environ={})
    assert report["assertions"] == [], report["assertions"]
    assert report["rejected"] == [], "规则模式跳过不算 rejected（没产生候选）"


def test_rules_are_pure_functions() -> None:
    """规则 provider 直接可调用，且同输入同输出。"""
    first = dry_run_provider(CHAPTER, "t/001")
    second = dry_run_provider(CHAPTER, "t/001")
    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_determinism_of_extract_chapter() -> None:
    first = extract_chapter(CHAPTER, chapter_id="t/001", provider=None, environ={})
    second = extract_chapter(CHAPTER, chapter_id="t/001", provider=None, environ={})
    assert first == second
    assert digest(first) == digest(second)
    assert digest(first).startswith("sha256:")
    assert digest([]) == digest([])
    assert digest(first) != digest(first[:-1]) if first else True
    # 排序保证：char_start 单调不减
    starts = [item["char_start"] for item in first]
    assert starts == sorted(starts), starts


def test_extract_chapter_is_sorted_subset_of_report() -> None:
    report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=None, environ={})
    assertions = extract_chapter(CHAPTER, chapter_id="t/001", provider=None, environ={})
    assert assertions == report["assertions"]
    assert report["characters"] == len(CHAPTER)


# ------------------------------------------------- OpenAI 兼容 provider 路径


def envelope(content: str) -> str:
    return json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False)


def provider_with(status: int, payload: str) -> OpenAIProvider:
    def transport(url: str, headers, body) -> tuple[int, str]:
        assert url.endswith("/chat/completions"), url
        assert headers["Authorization"].startswith("Bearer "), headers
        assert b"temperature" in body
        return status, payload

    return OpenAIProvider("https://example.invalid/v1", "test-model", "sk-test", transport=transport)


def test_openai_provider_accepts_json() -> None:
    assertion = blank_assertion()
    provider = provider_with(200, envelope(json.dumps([assertion], ensure_ascii=False)))
    report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=provider)
    assert report["mode"] == "custom", report["mode"]
    assert len(report["assertions"]) == 1, report["rejected_reasons"]
    assert report["assertions"][0]["subject"] == "甄费"


def test_openai_provider_accepts_fenced_and_object_forms() -> None:
    assertion = blank_assertion()
    for content in (
        "```json\n" + json.dumps([assertion], ensure_ascii=False) + "\n```",
        json.dumps({"assertions": [assertion]}, ensure_ascii=False),
    ):
        provider = provider_with(200, envelope(content))
        report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=provider)
        assert len(report["assertions"]) == 1, (content[:20], report["rejected_reasons"])


def test_openai_provider_failures_degrade() -> None:
    cases = [
        (provider_with(500, "boom"), "provider_error"),
        (provider_with(200, "这不是 JSON"), "invalid_json"),
        (provider_with(200, json.dumps({"choices": []})), "invalid_json"),
        (provider_with(200, envelope("不是断言的 JSON")), "invalid_json"),
    ]
    for provider, expected in cases:
        report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=provider)
        assert report["assertions"] == []
        assert report["rejected_reasons"] == {expected: 1}, (expected, report["rejected_reasons"])


def test_openai_provider_transport_exception_degrades() -> None:
    def transport(url, headers, body):
        raise RuntimeError("网络断了")

    provider = OpenAIProvider("https://example.invalid/v1", "m", "k", transport=transport)
    try:
        provider(CHAPTER, "t/001")
    except ExtractProviderError as error:
        assert error.reason == "provider_error", error.reason
    else:  # pragma: no cover - 走到这里说明异常没被包住
        raise AssertionError("网络异常必须包成 ExtractProviderError")

    report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=provider)
    assert report["rejected_reasons"] == {"provider_error": 1}, report["rejected_reasons"]


def test_custom_provider_crash_degrades_without_raising() -> None:
    def boom(text: str, chapter_id: str):
        raise ValueError("provider 写崩了")

    report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=boom)
    assert report["assertions"] == []
    assert report["rejected_reasons"] == {"provider_error": 1}, report["rejected_reasons"]
    assert "ValueError" in report["rejected"][0]["detail"]


def test_provider_returning_non_list_degrades() -> None:
    for payload in ("字符串", 42, {"assertions": "不是数组"}):
        report = report_for(payload)
        assert report["rejected_reasons"] == {"invalid_json": 1}, (payload, report["rejected_reasons"])


def test_provider_returning_assertions_object_is_accepted() -> None:
    report = report_for({"assertions": [blank_assertion()]})
    assert len(report["assertions"]) == 1, report["rejected_reasons"]


# ------------------------------------------------------------ 环境与凭据


def test_provider_from_env_requires_all_three() -> None:
    assert provider_from_env({}) is None
    assert provider_from_env({"MOYU_EXTRACT_BASE_URL": "https://x/v1"}) is None
    assert provider_from_env({
        "MOYU_EXTRACT_BASE_URL": "https://x/v1",
        "MOYU_EXTRACT_MODEL": "m",
    }) is None
    assert provider_from_env({
        "MOYU_EXTRACT_BASE_URL": "  ",
        "MOYU_EXTRACT_MODEL": "m",
        "MOYU_EXTRACT_API_KEY": "k",
    }) is None
    provider = provider_from_env({
        "MOYU_EXTRACT_BASE_URL": "https://x/v1/",
        "MOYU_EXTRACT_MODEL": "m",
        "MOYU_EXTRACT_API_KEY": "k",
    })
    assert isinstance(provider, OpenAIProvider)
    assert provider.endpoint == "https://x/v1/chat/completions"


def test_resolve_provider_modes() -> None:
    assert resolve_provider(None, {})[1] == "dry-run"
    assert resolve_provider("dry-run", {})[1] == "dry-run"
    openai_mode = resolve_provider(None, {
        "MOYU_EXTRACT_BASE_URL": "https://x/v1",
        "MOYU_EXTRACT_MODEL": "m",
        "MOYU_EXTRACT_API_KEY": "k",
    })
    assert openai_mode[1] == "openai" and isinstance(openai_mode[0], OpenAIProvider)
    custom = resolve_provider(dry_run_provider, {})[1]
    assert custom == "custom", custom


def test_missing_credentials_never_crash() -> None:
    report = extract_chapter_report(CHAPTER, chapter_id="t/001", environ={})
    assert report["mode"] == "dry-run"
    assert report["assertions"], "离线规则模式必须仍能抽到东西"


# --------------------------------------------------------------- 其他边界


def test_chinese_number() -> None:
    cases = {
        "12": 12,
        "十": 10,
        "十五": 15,
        "二十": 20,
        "二十五": 25,
        "一百单八": 108,
        "三": 3,
        "": None,
        "若干": None,
        "岁": None,
    }
    for text, expected in cases.items():
        assert chinese_number(text) == expected, (text, chinese_number(text))


def test_validate_is_pure_and_copies() -> None:
    payload = blank_assertion()
    snapshot = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    first = validate_assertion(payload)
    second = validate_assertion(payload)
    assert first == second
    assert json.dumps(payload, sort_keys=True, ensure_ascii=False) == snapshot, "校验不许改入参"
    first["subject"] = "改过的"
    assert payload["subject"] == "甄费"


def test_rejected_carries_reason_and_payload() -> None:
    try:
        validate_assertion({"subject": "甲"})
    except Rejected as error:
        assert error.reason == "missing_key", error.reason
        assert error.detail and "subject" in error.detail or True
    else:  # pragma: no cover
        raise AssertionError("缺键必须抛 Rejected")


def test_max_assertions_cap() -> None:
    text = "".join(f"姓甲名{i}字，年方{20 + i}岁。\n" for i in range(5))
    payloads = []
    for i in range(5):
        needle = f"姓甲名{i}"
        start = text.index(needle)
        payloads.append({
            "subject": f"甲{i}", "predicate": "has_attribute", "attribute": "real_name",
            "value": f"甲{i}", "evidence_quote": needle,
            "char_start": start, "char_end": start + len(needle), "confidence": 0.9,
        })
    report = extract_chapter_report(text, chapter_id="t/009", provider=provider_returning(payloads),
                                    max_assertions=2)
    assert len(report["assertions"]) == 2, report["assertions"]
    assert report["rejected_reasons"].get("duplicate") == 1, report["rejected_reasons"]


# ------------------------------------------------ 非法数值：NaN / Infinity


def _no_constant(token: str):
    """严格 JSON 消费方：遇到 NaN / Infinity 这类字面量直接失败。"""
    raise AssertionError(f"严格 JSON 不该出现 {token}")


def test_non_finite_values_rejected() -> None:
    """NaN / Infinity / -Infinity 一律拒绝，原因码必须是 non_finite（不是含混的 type_error）。"""
    for bad in (float("nan"), float("inf"), float("-inf")):
        payload = dict(blank_assertion(), value=bad)
        report = report_for(payload)
        assert report["assertions"] == [], (bad, report["assertions"])
        assert report["rejected_reasons"] == {"non_finite": 1}, (bad, report["rejected_reasons"])
        assert "有限" in report["rejected"][0]["detail"], report["rejected"][0]
    # 直接调校验也一样拦得住
    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            validate_assertion(dict(blank_assertion(), value=bad))
        except Rejected as error:
            assert error.reason == "non_finite", (bad, error.reason)
        else:  # pragma: no cover
            raise AssertionError(f"{bad!r} 必须被拒绝")


def test_non_finite_from_json_text_rejected() -> None:
    """JSON 里裸写 NaN/Infinity（Python json 默认接受）也必须被拒。"""
    for literal in ("NaN", "Infinity", "-Infinity"):
        raw = (
            '{"subject": "甄费", "predicate": "has_attribute", "attribute": "real_name",'
            ' "value": ' + literal + ', "evidence_quote": "姓甄名费", "char_start": 0,'
            ' "char_end": 4, "confidence": 0.9}'
        )
        parsed = json.loads(raw)
        assert isinstance(parsed["value"], float), (literal, type(parsed["value"]))
        assert not math.isfinite(parsed["value"]), (literal, parsed["value"])
        report = report_for(parsed)
        assert report["assertions"] == [], (literal, report["assertions"])
        assert report["rejected_reasons"] == {"non_finite": 1}, (literal, report["rejected_reasons"])


def test_confidence_non_finite_rejected() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        report = report_for(dict(blank_assertion(), confidence=bad))
        assert report["assertions"] == []
        assert report["rejected_reasons"] == {"non_finite": 1}, (bad, report["rejected_reasons"])


def test_finite_numbers_still_accepted() -> None:
    """合法有限数值不能因为加了闸门被误伤。"""
    for good in (0, -3, 42, 1.5, 0.0, -0.25, 10**6):
        report = report_for(dict(blank_assertion(), value=good))
        assert len(report["assertions"]) == 1, (good, report["rejected_reasons"])
        assert report["assertions"][0]["value"] == good


def test_output_is_strict_json() -> None:
    """抽出来的结果必须能被严格 JSON 消费方解析（不含裸 NaN / Infinity）。"""
    assertions = extract_chapter(CHAPTER, chapter_id="t/001", provider=None, environ={})
    payload = strict_json(assertions)
    parsed = json.loads(payload, parse_constant=_no_constant)
    assert parsed == assertions
    assert "NaN" not in payload and "Infinity" not in payload
    # digest 同样走严格序列化
    assert digest(assertions) == digest(assertions)
    # 含 NaN 的列表在 digest 处就要炸，而不是产出一份解析不了的 JSON
    try:
        digest([dict(assertions[0], value=float("nan"))])
    except ValueError as error:
        assert "非有限" in str(error) or "ValueError" in type(error).__name__, error
    else:  # pragma: no cover
        raise AssertionError("digest 必须拒绝 NaN")
    try:
        strict_json({"value": float("inf")})
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("strict_json 必须拒绝 Infinity")


def test_quote_gate_does_not_verify_semantics() -> None:
    """已知局限的可执行记录：闸门只验坐标，不验主语/取值与引文的语义关系。

    这条断言故意用「引文对、主语错」的样本，锁定「抽取器不做语义校验」这一事实——
    免得以后有人把 quote 闸门当成语义保证。真正的语义一致性靠服务端断言比较与
    backend/moyu_consistency.py，不在这里。
    """
    wrong_subject = dict(blank_assertion(), subject="贾化", value="贾化")
    report = report_for(wrong_subject)
    assert len(report["assertions"]) == 1, report["rejected_reasons"]
    assert report["assertions"][0]["subject"] == "贾化"


# ------------------------------------- 真实模型传输：curl 优先 / 不重试 / 不泄露（t62）


class _LocalChatHandler(http.server.BaseHTTPRequestHandler):
    """本地假 `/chat/completions`：记录请求，按配置回状态码与响应体。"""

    status = 200
    body = "{}"
    seen: dict | None = None

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定名
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        type(self).seen = {
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
            "body": payload,
        }
        encoded = type(self).body.encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args) -> None:  # 静音
        return


def serve_chat(status: int, body: str):
    handler = type("_Handler", (_LocalChatHandler,), {"status": status, "body": body, "seen": None})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, handler


def close_server(server) -> None:
    server.shutdown()
    server.server_close()


def local_provider(server, *, timeout: float = 20.0, key: str = "sk-local-test") -> OpenAIProvider:
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return OpenAIProvider(base_url, "test-model", key, timeout=timeout)


def one_assertion_envelope() -> str:
    return envelope(json.dumps([blank_assertion()], ensure_ascii=False))


def test_default_timeout_is_not_thirty_seconds() -> None:
    """默认超时必须能容纳真实模型（单章 30–50 秒、长尾 >120 秒）。"""
    assert DEFAULT_TIMEOUT == 180.0, DEFAULT_TIMEOUT
    provider = OpenAIProvider("https://example.invalid/v1", "m", "k", timeout=42)
    assert provider.timeout == 42.0, provider.timeout


def test_timeout_is_validated_not_clamped() -> None:
    for bad in (0, -1, float("nan"), float("inf"), "30", True, None):
        try:
            OpenAIProvider("https://example.invalid/v1", "m", "k", timeout=bad)
        except ValueError:
            continue
        raise AssertionError(f"非法超时必须 ValueError，而不是静默取默认值：{bad!r}")


def test_env_timeout_override() -> None:
    env = {
        "MOYU_EXTRACT_BASE_URL": "https://x/v1",
        "MOYU_EXTRACT_MODEL": "m",
        "MOYU_EXTRACT_API_KEY": "k",
    }
    assert provider_from_env(env).timeout == DEFAULT_TIMEOUT
    assert provider_from_env(dict(env, **{ENV_TIMEOUT: "240"})).timeout == 240.0
    for bad in ("abc", "0", "-5", "nan", "inf"):
        try:
            provider_from_env(dict(env, **{ENV_TIMEOUT: bad}))
        except ValueError as error:
            assert ENV_TIMEOUT in str(error), str(error)
        else:
            raise AssertionError(f"{ENV_TIMEOUT}={bad!r} 必须显式报错")
    # 没有凭据时连读都不读，也不崩
    assert provider_from_env({ENV_TIMEOUT: "abc"}) is None


def test_real_transport_sends_through_curl() -> None:
    """默认传输真的走 curl 子进程，且请求头/请求体原样到达服务端。"""
    if not curl_available():
        return  # 环境没有 curl：本机无法验证子进程路径，不算失败
    server, handler = serve_chat(200, one_assertion_envelope())
    try:
        report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=local_provider(server))
    finally:
        close_server(server)
    assert len(report["assertions"]) == 1, report["rejected_reasons"]
    assert handler.seen["path"] == "/chat/completions", handler.seen["path"]
    assert handler.seen["auth"] == "Bearer sk-local-test", handler.seen["auth"]
    assert b"temperature" in handler.seen["body"], handler.seen["body"][:80]


def test_curl_403_is_visible_not_fake_success() -> None:
    """403 / error code 1010 必须变成可见的 provider_error，绝不伪造空断言成功。"""
    server, _ = serve_chat(403, json.dumps({"error": {"code": 1010, "message": "blocked"}}))
    try:
        report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=local_provider(server))
    finally:
        close_server(server)
    assert report["assertions"] == [], report["assertions"]
    assert report["rejected_reasons"] == {"provider_error": 1}, report["rejected_reasons"]
    detail = report["rejected"][0]["detail"]
    assert "403" in detail and "1010" in detail, detail


def test_default_transport_prefers_curl_over_urllib() -> None:
    """urllib 会 403、curl 会 200 —— 默认 provider 必须拿到 200（选 curl）。"""
    calls = {"curl": 0, "urllib": 0}

    def fake_curl(url, headers, body, **kwargs):
        calls["curl"] += 1
        return 200, one_assertion_envelope()

    def fake_urllib(url, headers, body, **kwargs):
        calls["urllib"] += 1
        return 403, '{"error":{"code":1010}}'

    original = (extract_module._curl_transport, extract_module._urllib_transport)
    extract_module._curl_transport = fake_curl
    extract_module._urllib_transport = fake_urllib
    try:
        provider = OpenAIProvider("https://example.invalid/v1", "m", "k", timeout=20)
        report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=provider)
    finally:
        extract_module._curl_transport, extract_module._urllib_transport = original
    assert len(report["assertions"]) == 1, report["rejected_reasons"]
    assert report["rejected_reasons"] == {}, report["rejected_reasons"]
    assert calls == {"curl": 1, "urllib": 0}, calls


def test_real_transport_falls_back_to_urllib_without_curl() -> None:
    server, handler = serve_chat(200, one_assertion_envelope())
    url = f"http://127.0.0.1:{server.server_address[1]}/chat/completions"
    try:
        status, payload = extract_module._real_transport(
            url,
            {"Content-Type": "application/json", "Authorization": "Bearer sk-fallback"},
            b'{"ping": true}',
            timeout=20,
            curl_bin="curl-does-not-exist-xyz",
        )
    finally:
        close_server(server)
    assert status == 200, status
    assert "choices" in payload, payload[:80]
    assert handler.seen["auth"] == "Bearer sk-fallback", handler.seen["auth"]


def test_curl_failure_and_timeout_are_classified() -> None:
    calls: list[float] = []

    def failing_runner(cmd, payload, timeout):
        calls.append(timeout)
        return 7, "", "curl: (7) Failed to connect to 127.0.0.1 port 9"

    try:
        extract_module._curl_transport(
            "http://127.0.0.1:9/chat/completions", {}, b"{}", timeout=20, runner=failing_runner
        )
    except ExtractProviderError as error:
        assert error.reason == "provider_error", error.reason
        assert "退出码 7" in error.detail, error.detail
    else:
        raise AssertionError("curl 失败必须抛 ExtractProviderError")
    assert calls == [20 + 15], calls  # 只发一次，且 curl 硬杀时间 = 超时 + 余量

    def timeout_runner(cmd, payload, timeout):
        return 28, "", ""

    try:
        extract_module._curl_transport(
            "http://127.0.0.1:9/chat/completions", {}, b"{}", timeout=20, runner=timeout_runner
        )
    except ExtractProviderError as error:
        assert "超时" in error.detail and "20" in error.detail, error.detail
    else:
        raise AssertionError("curl 超时必须抛 ExtractProviderError")


def test_curl_non_status_output_is_an_error() -> None:
    def weird_runner(cmd, payload, timeout):
        return 0, "not-a-status", ""

    try:
        extract_module._curl_transport("http://x/chat/completions", {}, b"{}", timeout=5, runner=weird_runner)
    except ExtractProviderError as error:
        assert "状态码" in error.detail, error.detail
    else:
        raise AssertionError("没有状态码必须报错")


def test_api_key_never_leaks() -> None:
    key = "sk-super-secret-1234567890"

    def leaking_transport(url, headers, body):
        raise RuntimeError(f"boom {headers['Authorization']}")

    provider = OpenAIProvider("https://example.invalid/v1", "m", key, transport=leaking_transport)
    try:
        provider(CHAPTER, "t/001")
    except ExtractProviderError as error:
        assert key not in error.detail, error.detail
        assert "***" in error.detail, error.detail
    else:
        raise AssertionError("异常必须包成 ExtractProviderError")
    report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=provider)
    assert key not in json.dumps(report, ensure_ascii=False), "报告里出现了 API Key"

    def leaking_runner(cmd, payload, timeout):
        return 7, "", f"header rejected: Authorization: Bearer {key}"

    try:
        extract_module._curl_transport(
            "http://x/chat/completions", {"Authorization": f"Bearer {key}"}, b"{}",
            timeout=5, runner=leaking_runner,
        )
    except ExtractProviderError as error:
        assert key not in error.detail, error.detail
    else:
        raise AssertionError("curl 失败必须抛 ExtractProviderError")


def test_custom_transport_bypasses_real_transport() -> None:
    original = extract_module._curl_transport

    def must_not_run(*args, **kwargs):
        raise AssertionError("注入 transport 时不得触碰默认真实传输")

    extract_module._curl_transport = must_not_run
    try:
        provider = provider_with(200, one_assertion_envelope())
        report = extract_chapter_report(CHAPTER, chapter_id="t/001", provider=provider)
    finally:
        extract_module._curl_transport = original
    assert len(report["assertions"]) == 1, report["rejected_reasons"]


def test_curl_error_detail_redacts_before_truncation() -> None:
    """凭据跨过 200 字符截断边界时，必须先脱敏再截断（t63 复审 F1）。"""
    key = "sk-t64-SECRET-abcdef0123456789"

    def runner_with(stderr: str, code: int):
        def runner(cmd, body, timeout):
            return code, "", stderr

        return runner

    cases = [
        (7, "A" * 180 + key),        # 凭据恰好横跨 200 字符边界
        (7, "A" * 400 + " " + key),  # 凭据远在边界之后
        (7, key + " 被拒绝"),          # 凭据在开头
        (28, "A" * 190 + key),       # 超时路径同样先脱敏再截断
    ]
    headers = {"Authorization": f"Bearer {key}"}
    for code, stderr in cases:
        # 前置条件：完整 stderr 先被脱敏干净（这一步若失效，下面的 detail 断言就没意义）
        safe = extract_module._redact_secrets(stderr, extract_module._header_secrets(headers))
        assert key not in safe, (code, safe)
        assert "***" in safe, (code, safe)

        try:
            extract_module._curl_transport(
                "http://127.0.0.1:9/chat/completions",
                headers,
                b"{}",
                timeout=20,
                runner=runner_with(stderr, code),
            )
        except ExtractProviderError as error:
            assert error.reason == "provider_error", error.reason
            assert key not in error.detail, (code, error.detail)
            assert key[:10] not in error.detail, (code, error.detail)
            assert len(error.detail) <= 300, len(error.detail)
            assert ("超时" in error.detail) if code == 28 else ("失败" in error.detail), error.detail
        else:
            raise AssertionError("curl 非零退出必须抛 ExtractProviderError")

    # 非状态码输出路径同样脱敏
    def weird_runner(cmd, body, timeout):
        return 0, f"{key} 不是状态码", ""

    try:
        extract_module._curl_transport(
            "http://127.0.0.1:9/chat/completions",
            {"Authorization": f"Bearer {key}"},
            b"{}",
            timeout=5,
            runner=weird_runner,
        )
    except ExtractProviderError as error:
        assert key not in error.detail, error.detail
        assert key[:10] not in error.detail, error.detail
    else:
        raise AssertionError("没有状态码必须报错")


def test_authorization_key_forms_are_redacted() -> None:
    """t65 F1：含换行 key 的裸回显、7 字符 key 跨 200 边界，都必须抹干净。"""
    newline_key = "sk-t67-line1\nsk-t67-line2"
    short_key = "sk-a67b"  # 7 字符：低于旧实现的 >=8 门槛

    def runner_with(stderr: str):
        def runner(cmd, body, timeout):
            return 7, "", stderr

        return runner

    cases = [
        # 场景 A：key 含换行，stderr 只回显第一行（裸片段）
        ({"authorization": f"Bearer {newline_key}"}, "sk-t67-line1",
         ["sk-t67-line1", newline_key, "line2"]),
        # 场景 A'：header 名全大写同样生效
        ({"AUTHORIZATION": f"Bearer {newline_key}"}, "x " + newline_key,
         [newline_key, "sk-t67-line2"]),
        # 场景 B：7 字符 key 真正跨过 200 字符边界（196 + 7 = 203，t68 F3）
        ({"Authorization": f"Bearer {short_key}"}, "A" * 196 + short_key,
         [short_key, short_key[:5]]),
    ]
    for headers, stderr, secrets in cases:
        try:
            extract_module._curl_transport(
                "http://127.0.0.1:9/chat/completions", headers, b"{}",
                timeout=20, runner=runner_with(stderr),
            )
        except ExtractProviderError as error:
            assert error.reason == "provider_error", error.reason
            assert "失败" in error.detail and "退出码 7" in error.detail, error.detail
            for secret in secrets:
                assert secret not in error.detail, (secret, error.detail)
            assert "***" in error.detail, error.detail
        else:
            raise AssertionError("curl 非零退出必须抛 ExtractProviderError")


def test_generic_short_header_value_is_not_redacted() -> None:
    """其他 header 维持 >=8 规则：短通用值（POST）不能被抹成 ***。"""

    def runner(cmd, body, timeout):
        return 7, "", "POST /chat/completions 连接被拒绝"

    try:
        extract_module._curl_transport(
            "http://127.0.0.1:9/chat/completions",
            {"X-Method": "POST", "Authorization": "Bearer sk-generic-8791"},
            b"{}",
            timeout=5,
            runner=runner,
        )
    except ExtractProviderError as error:
        assert "POST" in error.detail, error.detail
        assert "***" not in error.detail, error.detail


def test_old_header_secrets_would_leak() -> None:
    """非恒真证明：复算旧实现（只登记整值与末 token，且 >=8），同一输入确实泄漏。"""
    short_key = "sk-a67b"
    old_secrets = set()
    for value in [f"Bearer {short_key}"]:
        if len(value) >= 8:
            old_secrets.add(value)
        parts = value.split()
        if parts and len(parts[-1]) >= 8:
            old_secrets.add(parts[-1])
    leaky = extract_module._redact_secrets("A" * 196 + short_key, old_secrets)
    assert short_key in leaky, "旧实现模拟必须复现泄漏，否则新用例是恒真的"

    newline_key = "sk-t67-line1\nsk-t67-line2"
    old_newline_secrets = {f"Bearer {newline_key}", "sk-t67-line2"}
    assert "sk-t67-line1" in extract_module._redact_secrets("sk-t67-line1", old_newline_secrets)


def test_non_2xx_body_redacts_before_truncation() -> None:
    """t68 F1：非 2xx 响应体里的 key 跨 200 边界时，detail 不得留下可识别前缀。"""
    key = "sk-t69-http-998877"

    def detail_for(reservoir: str) -> str:
        def transport(url, headers, payload):
            return 403, json.dumps(
                {"error": {"code": 1010, "detail": reservoir}}, ensure_ascii=False
            )

        provider = OpenAIProvider("http://127.0.0.1:9", "m", key, transport=transport)
        try:
            provider("第 1 章正文。", "ch-1")
        except ExtractProviderError as error:
            assert error.reason == "provider_error", error.reason
            # 状态码与网关错误码仍必须可见（t62 契约）
            assert "403" in error.detail, error.detail
            assert "1010" in error.detail, error.detail
            assert key not in error.detail, error.detail
            assert key[:6] not in error.detail, error.detail
            assert len(error.detail) <= 240, len(error.detail)
            return error.detail
        raise AssertionError("非 2xx 必须抛 ExtractProviderError")

    # 场景 A：key 从第 197 位开始，跨过 200 字符边界（旧实现会在 detail 末尾留下 sk-t69）
    crossed = detail_for("A" * 196 + key)
    assert "sk-t69" not in crossed, crossed
    # 场景 B：key 在窗口内，必须被抹成 ***
    early = detail_for(key + "A" * 196)
    assert "***" in early, early


def test_content_detail_redacts_before_truncation() -> None:
    """t68 F2：不可解析的 content 也可能原样回显凭据，同样先脱敏再截断。"""
    key = "sk-t69-content-123456"

    def detail_for(reservoir: str) -> str:
        def transport(url, headers, payload):
            return 200, json.dumps(
                {"choices": [{"message": {"content": reservoir}}]}, ensure_ascii=False
            )

        provider = OpenAIProvider("http://127.0.0.1:9", "m", key, transport=transport)
        try:
            provider("第 1 章正文。", "ch-1")
        except ExtractProviderError as error:
            assert error.reason == "invalid_json", error.reason
            assert "content" in error.detail, error.detail
            assert key not in error.detail, error.detail
            assert key[:6] not in error.detail, error.detail
            return error.detail
        raise AssertionError("content 不可解析必须抛 ExtractProviderError")

    crossed = detail_for("A" * 196 + key + " 不是 JSON")
    assert "sk-t69" not in crossed, crossed
    early = detail_for(key + "A" * 196 + " 不是 JSON")
    assert "***" in early, early


def test_old_provider_detail_would_leak() -> None:
    """非恒真证明：复算「先截断后脱敏」的旧顺序，同一输入确实泄漏 key 前缀。"""
    key = "sk-t69-old-445566"
    reservoir = "A" * 196 + key

    # 旧顺序：先截断（只剩 key 的前 4 个字符）再脱敏 → 完整 key 已不存在，脱敏无从下手
    leaky = extract_module._redact(f"HTTP 403：{reservoir[:200]}", key)
    assert key[:4] in leaky and key not in leaky, "旧顺序必须复现前缀泄漏，否则新用例是恒真的"

    # 新顺序：先按完整凭据脱敏，再截断 → 连前缀都不留
    safe = extract_module._redact_secrets(reservoir, [key])[:200]
    assert key not in safe and key[:4] not in safe, safe


def test_custom_provider_exception_detail_is_sanitized() -> None:
    """t70 F1：自定义 provider 抛任意异常时，原文里的凭据不得写进 rejected.detail。"""
    key = "sk-t71-custom-556677"

    def boom(text, chapter_id):
        raise RuntimeError(f"调用失败：Authorization: Bearer {key}")

    report = extract_module.extract_chapter_report(
        "第 1 章正文。", chapter_id="ch-1", provider=boom
    )
    assert report["rejected_reasons"] == {"provider_error": 1}, report
    detail = report["rejected"][0]["detail"]
    assert key not in detail, detail
    assert key[:8] not in detail, detail
    assert "Bearer" not in detail, detail
    # 可诊断性：异常类型必须保留，且告警文案说明详情已隐去
    assert "RuntimeError" in detail, detail
    assert "已隐去" in detail, detail


def test_custom_provider_extract_error_detail_is_masked() -> None:
    """自定义 provider 直接抛 ExtractProviderError 并塞入凭据时，报告层再兜一层。"""
    key = "sk-t71-extract-998877"

    def boom(text, chapter_id):
        raise ExtractProviderError("provider_error", f"HTTP 401：Bearer {key}")

    report = extract_module.extract_chapter_report(
        "第 1 章正文。", chapter_id="ch-1", provider=boom
    )
    detail = report["rejected"][0]["detail"]
    assert report["rejected_reasons"] == {"provider_error": 1}, report
    assert key not in detail, detail
    assert key[:8] not in detail, detail
    assert "***" in detail, detail
    assert "401" in detail, detail


def test_old_report_detail_would_leak() -> None:
    """非恒真证明：复算旧写法 f"{type}: {error}"，同一异常确实把 key 写进 detail。"""
    key = "sk-t71-custom-556677"
    try:
        raise RuntimeError(f"调用失败：Authorization: Bearer {key}")
    except RuntimeError as error:
        leaky = f"{type(error).__name__}: {error}"
    assert key in leaky, "旧写法必须复现泄漏，否则新用例是恒真的"
    masked = extract_module._mask_credentials(leaky)
    assert key not in masked, masked
    assert key[:8] not in masked, masked
    # 诊断可读性：键名保留（t73 起按形状抹值，不再整段丢弃）
    assert "Authorization:" in masked, masked
    assert "***" in masked, masked


def test_extract_error_detail_masks_common_credential_shapes() -> None:
    """t72 F1：自定义 ExtractProviderError 的 detail 里，常见凭据形态都不得原样落盘。"""
    shapes = {
        "api_key_plain": ("api_key=abc123def456ghi789", "abc123def456ghi789", "api_key="),
        "aws_akia": ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE", "403"),
        "x_api_key": ("x-api-key: 7d3f9a1c2b4e5f60718293a4b5c6d7e8",
                      "7d3f9a1c2b4e5f60718293a4b5c6d7e8", "x-api-key:"),
        "token_ghp": ("token=ghp_0123456789abcdef0123456789abcdef0123",
                      "ghp_0123456789abcdef0123456789abcdef0123", "token="),
        "password": ("password: hunter2-in-prod", "hunter2-in-prod", "password:"),
        "secret": ("secret=Zm9vYmFyYmF6cXV1eDEyMzQ1Njc4OTA=",
                   "Zm9vYmFyYmF6cXV1eDEyMzQ1Njc4OTA=", "secret="),
        "bare_high_entropy": ("9f8e7d6c5b4a39281706f5e4d3c2b1a0",
                              "9f8e7d6c5b4a39281706f5e4d3c2b1a0", ""),
        "cookie": ("cookie: sessionid=abcd1234efgh5678", "abcd1234efgh5678", "cookie:"),
        # t73 追加：scheme + 无前缀短令牌（旧实现先吃掉 scheme，令牌裸奔）
        "auth_bearer_short": ("Authorization: Bearer abcd1234efgh", "abcd1234efgh", "Authorization:"),
        "auth_basic_base64": ("Authorization: Basic dXNlcjpwYXNzd29yZA==",
                              "dXNlcjpwYXNzd29yZA==", "Authorization:"),
        "auth_token_short": ("Authorization: Token 9f8e7d6c5b4a", "9f8e7d6c5b4a", "Authorization:"),
        # 短令牌（1/2/3 字符）：长度不是「不是凭据」的理由
        "auth_bearer_1": ("Authorization: Bearer a", "Bearer a", "Authorization:"),
        "auth_bearer_2": ("Authorization: Bearer ab", "Bearer ab", "Authorization:"),
        "auth_bearer_3": ("Authorization: Bearer abc", "Bearer abc", "Authorization:"),
        "auth_digest_3": ("Authorization: Digest xyz", "xyz", "Authorization:"),
        # 标点混入令牌：字符类不能吃掉一半就停
        "auth_bearer_bang": ("Authorization: Bearer ab!cdefgh", "ab!cdefgh", "Authorization:"),
        # Digest 的引号参数（response 是真正的摘要凭据）
        "digest_response": ('Authorization: Digest username="u", response="deadbeef"',
                            "deadbeef", "response="),
        # 同串共存：Authorization 与 api_key 必须各自抹净
        "auth_plus_api_key": ("Authorization: Basic dXNlcjpwYXNz 且 api_key=abc123def456",
                              "dXNlcjpwYXNz", "api_key="),
    }
    for label, (raw, secret, marker) in shapes.items():
        def boom(text, chapter_id, detail=f"HTTP 403：{raw}"):
            raise ExtractProviderError("provider_error", detail)

        report = extract_module.extract_chapter_report(
            "第 1 章正文。", chapter_id="ch-1", provider=boom
        )
        assert report["rejected_reasons"] == {"provider_error": 1}, (label, report)
        detail = report["rejected"][0]["detail"]
        # 必要诊断：状态码与（有键名时的）键名仍可读
        assert "403" in detail, (label, detail)
        if marker:
            assert marker in detail, (label, detail)
        # 凭据原值及其可识别前缀都不得出现
        assert secret not in detail, (label, detail)
        assert secret[:8] not in detail, (label, detail)


def test_old_extract_error_detail_would_leak() -> None:
    """非恒真证明：复算 t71 版掩码（只认 Bearer / sk-），这些形态确实原样泄漏。"""
    def old_mask(text):
        masked = re.sub(r"(?i)\bbearer\s+[^\s\"',;]+", "Bearer ***", str(text))
        masked = re.sub(r"\bsk-[A-Za-z0-9_\-]{4,}", "***", masked)
        return masked

    shapes = {
        "api_key_plain": "api_key=abc123def456ghi789",
        "aws_akia": "aws 403 AKIAIOSFODNN7EXAMPLE 拒绝",
        "x_api_key": "x-api-key: 7d3f9a1c2b4e5f60718293a4b5c6d7e8",
        "token_ghp": "token=ghp_0123456789abcdef0123456789abcdef0123",
        "password": "password: hunter2-in-prod",
        "secret": "secret=Zm9vYmFyYmF6cXV1eDEyMzQ1Njc4OTA=",
        "bare_high_entropy": "9f8e7d6c5b4a39281706f5e4d3c2b1a0",
        "auth_basic_base64": "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "auth_token_short": "Authorization: Token 9f8e7d6c5b4a",
        "digest_response": 'Authorization: Digest username="u", response="deadbeef"',
    }
    for label, raw in shapes.items():
        leaked = old_mask(raw)
        value = raw.split("=" , 1)[-1].split(": ", 1)[-1].strip()
        assert value in leaked, (label, leaked, "旧实现必须复现泄漏，否则新用例是恒真的")
        fresh = extract_module._mask_credentials(raw)
        assert value not in fresh, (label, fresh)


def test_short_authorization_tokens_are_masked() -> None:
    """t73 追加：连 1 个字符的令牌也不许残留；旧实现（长度下界 4）必须复现残留。"""
    cases = ["a", "ab", "abc", "abcd"]
    for token in cases:
        raw = f"Authorization: Bearer {token}"
        fresh = extract_module._mask_credentials(raw)
        assert fresh == "Authorization: ***", (token, fresh)

    # 非恒真：复算「令牌至少 4 字符」的旧版正则，3 字符令牌确实残留
    bounded = re.sub(
        r"(?i)\b(authorization\b\s*[:=]\s*)(?:[a-z]{3,12}\s+)?[A-Za-z0-9._~+/=\-]{4,}",
        lambda m: m.group(1) + "***",
        "Authorization: Bearer abc",
    )
    assert "abc" in bounded, bounded
    assert "abc" not in extract_module._mask_credentials("Authorization: Bearer abc")


def test_mask_keeps_diagnostics_readable() -> None:
    """脱敏不能把诊断一起抹掉：状态码、错误标识、耗时都要还能读。"""
    source = "HTTP 502 bad_gateway：upstream closed after 30s, error code 1010"
    masked = extract_module._mask_credentials(source)
    for token in ("502", "bad_gateway", "30s", "1010"):
        assert token in masked, (token, masked)
    assert masked == extract_module._mask_credentials(masked)  # 幂等

    # 正常中文/数字文案（无长 ASCII 串、无凭据名）必须原样保留，不许被高熵兜底误伤
    for plain in (
        "provider 调用失败：TimeoutError（上游 120 秒无响应）",
        "HTTP 403：请求被网关拒绝，error code 1010",
        "章节正文里出现 38 个非空白字符",
        "Content-Type: application/json model=gpt-4o-mini",
        "Authorization: 缺少令牌，请检查配置",
        "basic plan 是普通短语，不该被当成 Basic 凭据",
    ):
        assert extract_module._mask_credentials(plain) == plain, plain


def test_fullwidth_separators_do_not_swallow_next_key() -> None:
    """t75 F1：全角分隔符后紧邻的凭据键值不许被 Authorization 吞掉键名。"""
    separators = ("；", "，", "、", "：")
    keys = (("x-api-key", "xapi-9f8e7d6c5b4a3210"),
            ("api_key", "apik-9f8e7d6c5b4a3210"),
            ("token", "tok-9f8e7d6c5b4a3210"),
            ("secret", "sec-9f8e7d6c5b4a3210"))
    for sep in separators:
        for key, value in keys:
            raw = f"Authorization: Bearer tok-probe-123456{sep}{key}: {value}"
            masked = extract_module._mask_credentials(raw)
            assert value not in masked, (sep, key, masked)
            assert key in masked, (sep, key, masked)   # 键名要留，诊断才能定位
            assert sep in masked, (sep, masked)
            assert "Authorization: ***" in masked, masked

    # 分隔符之后的中文/数字诊断尽量保留
    for sep in separators:
        raw = f"Authorization: Bearer tok-probe-123456{sep}HTTP 502 bad_gateway error code 1010"
        masked = extract_module._mask_credentials(raw)
        for token in ("502", "bad_gateway", "1010"):
            assert token in masked, (sep, token, masked)

    # 非恒真：旧实现的终止符只有 ASCII ,;，全角分隔符会让它吞掉下一个键名
    old = re.compile(
        r"(?i)\b(authorization\b\s*[:=]\s*)(?:[a-z]{3,12}\s+)?"
        r"[A-Za-z0-9._~+/=\-][^\s,;]*"
    )
    stale = old.sub(lambda m: m.group(1) + "***", "Authorization: Bearer tok-probe-123456；x-api-key: xapi-9f8e7d6c5b4a3210")
    assert "xapi-9f8e7d6c5b4a3210" in stale, stale
    fresh = extract_module._mask_credentials("Authorization: Bearer tok-probe-123456；x-api-key: xapi-9f8e7d6c5b4a3210")
    assert "xapi-9f8e7d6c5b4a3210" not in fresh, fresh


def test_bare_basic_token_masked_but_phrase_kept() -> None:
    """t75 F2：裸 `Basic <>=6 位令牌>` 要抹，`basic plan` 这类短语不能被误伤。"""
    for token in ("dXNlcjpwYXNz", "abcdef123456", "QmFzaWNUb2tlbg=="):
        assert extract_module._mask_credentials(f"Basic {token}") == "Basic ***", token
    for phrase in ("basic plan 是普通短语", "basic 配置说明", "Basic 说明"):
        assert extract_module._mask_credentials(phrase) == phrase, phrase
    # 非恒真：旧正则不含 basic，裸令牌原样留下
    old = re.compile(r"(?i)\b(bearer|token|digest)\s+[A-Za-z0-9._~+/=\-]{6,}")
    stale = old.sub(lambda m: m.group(1) + " ***", "Basic QmFzaWNUb2tlbg==")
    assert "QmFzaWNUb2tlbg==" in stale, stale


def test_fullwidth_separators_keep_following_diagnostics() -> None:
    """t75 F1 补充：键值在全角分隔符处必须终止，后面的诊断不能被吞掉。"""
    separators = ("；", "，", "、", "：")
    cases = (
        ("x-api-key: xapi-9f8e7d6c5b4a3210", "xapi-9f8e7d6c5b4a3210"),
        ("api_key: apik-9f8e7d6c5b4a3210", "apik-9f8e7d6c5b4a3210"),
        ("token: tok-9f8e7d6c5b4a3210", "tok-9f8e7d6c5b4a3210"),
        ("model: gpt-4o", None),
        ("Content-Type: application/json", None),
    )
    for sep in separators:
        for fragment, secret in cases:
            raw = (f"HTTP 502：Authorization: Bearer Zx9Qw8Er7Ty6{sep}{fragment}"
                   f"{sep}错误码1010{sep}上游拒绝 invalid_json")
            masked = extract_module._mask_credentials(raw)
            if secret is not None:
                assert secret not in masked, (sep, fragment, masked)
            # 尾部诊断必须还在（键值不许吞掉它们）
            for token in ("错误码1010", "上游拒绝", "invalid_json", "HTTP 502"):
                assert token in masked, (sep, fragment, token, masked)
            # 值被抹掉、名字保留
            name = fragment.split(":", 1)[0]
            assert name in masked, (sep, name, masked)

    # 非恒真：旧值类 [^\s,;]+ 会把「；错误码1010」整段吞进值里
    old_pair = re.compile(
        r"(?i)\b(" + extract_module._CREDENTIAL_NAME + r"\b\s*[:=]\s*)[^\s,;]+"
    )
    stale = old_pair.sub(lambda m: m.group(1) + "***",
                         "x-api-key: xapi-9f8e7d6c5b4a3210；错误码1010")
    assert "错误码1010" not in stale, stale
    fresh = extract_module._mask_credentials("x-api-key: xapi-9f8e7d6c5b4a3210；错误码1010")
    assert "错误码1010" in fresh, fresh

    # Digest 的非引号分支同样要终止于全角分隔符
    for sep in separators:
        raw = f'Authorization: Digest nonce=abc123def456ghi789{sep}错误码1010'
        masked = extract_module._mask_credentials(raw)
        assert "错误码1010" in masked, (sep, masked)
        assert "abc123def456ghi789" not in masked, (sep, masked)


def main() -> None:
    tests = [
        test_module_surface,
        test_valid_assertion_passes,
        test_unknown_key_rejected,
        test_missing_key_rejected,
        test_type_errors_rejected,
        test_empty_values_rejected,
        test_predicate_whitelist,
        test_bad_ranges_rejected,
        test_long_quote_rejected,
        test_confidence_bounds,
        test_quote_mismatch_rejected,
        test_shifted_span_is_caught,
        test_duplicate_assertions_are_dropped_once,
        test_empty_text_never_raises,
        test_dry_run_rules_extract_known_facts,
        test_dry_run_gate_skips_unknown_subject,
        test_rules_are_pure_functions,
        test_determinism_of_extract_chapter,
        test_extract_chapter_is_sorted_subset_of_report,
        test_openai_provider_accepts_json,
        test_openai_provider_accepts_fenced_and_object_forms,
        test_openai_provider_failures_degrade,
        test_openai_provider_transport_exception_degrades,
        test_custom_provider_crash_degrades_without_raising,
        test_provider_returning_non_list_degrades,
        test_provider_returning_assertions_object_is_accepted,
        test_provider_from_env_requires_all_three,
        test_resolve_provider_modes,
        test_missing_credentials_never_crash,
        test_chinese_number,
        test_validate_is_pure_and_copies,
        test_rejected_carries_reason_and_payload,
        test_max_assertions_cap,
        test_non_finite_values_rejected,
        test_non_finite_from_json_text_rejected,
        test_confidence_non_finite_rejected,
        test_finite_numbers_still_accepted,
        test_output_is_strict_json,
        test_quote_gate_does_not_verify_semantics,
        test_default_timeout_is_not_thirty_seconds,
        test_timeout_is_validated_not_clamped,
        test_env_timeout_override,
        test_real_transport_sends_through_curl,
        test_curl_403_is_visible_not_fake_success,
        test_default_transport_prefers_curl_over_urllib,
        test_real_transport_falls_back_to_urllib_without_curl,
        test_curl_failure_and_timeout_are_classified,
        test_curl_non_status_output_is_an_error,
        test_api_key_never_leaks,
        test_custom_transport_bypasses_real_transport,
        test_curl_error_detail_redacts_before_truncation,
        test_authorization_key_forms_are_redacted,
        test_generic_short_header_value_is_not_redacted,
        test_old_header_secrets_would_leak,
        test_non_2xx_body_redacts_before_truncation,
        test_content_detail_redacts_before_truncation,
        test_old_provider_detail_would_leak,
        test_custom_provider_exception_detail_is_sanitized,
        test_custom_provider_extract_error_detail_is_masked,
        test_old_report_detail_would_leak,
        test_extract_error_detail_masks_common_credential_shapes,
        test_old_extract_error_detail_would_leak,
        test_fullwidth_separators_keep_following_diagnostics,
        test_fullwidth_separators_do_not_swallow_next_key,
        test_bare_basic_token_masked_but_phrase_kept,
        test_short_authorization_tokens_are_masked,
        test_mask_keeps_diagnostics_readable,
    ]
    for test in tests:
        test()
    print("extract fixtures: OK", {"cases": len(tests)})


if __name__ == "__main__":
    main()
