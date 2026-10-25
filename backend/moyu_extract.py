"""章节正文 → 结构化断言：可离线跑、可接 OpenAI 兼容接口、字符级证据校验。

为什么单独一个模块
------------------
`backend/moyu_consistency.py` 读的是 `assertion` 表，而那张表靠「AI 把正文抽成断言」
才有内容。抽取这一步此前没有实现：要么整段跳过，要么在基准里手写死数据。这个模块
把它做成**独立、可测、可离线**的一层，并且只依赖标准库（`urllib` 调 HTTP），不进
生产 `server.py` 的导入链——接线由服务端负责，这里只保证「抽得出来、引文可逐字定位」。

三条硬约束（都是代码，不是文档）
--------------------------------
1. **证据可定位（只到「可定位」，不到「语义正确」）**：每条断言的 `evidence_quote`
   必须能在该章原文里按 `char_start` / `char_end` 精确切出来，且逐字相等。切不出来
   （越界、错位、模型改写了一个字）一律丢弃，并按原因分类计数——
   `extract_chapter_report()` 返回 `rejected_reasons`。
   **这条闸门证明的是「这串引文确实出自本章的这个码点区间」，不是「这条断言的
   subject / attribute / value 与引文语义相符」**：把「姓甄名费」标成 `subject=贾化`
   照样能通过闸门。语义锚定不在本模块能力范围内，见下节「能力边界」。
2. **确定性**：`provider=None` 时走 `dry_run_provider()`（确定性规则），同一输入两次
   调用结果逐字节相同：断言列表按 (char_start, char_end, subject, predicate,
   attribute, value) 排序，`attribute` 缺省统一成 `None`，数字不转字符串。
3. **离线优先**：没有 `MOYU_EXTRACT_*` 凭据时，默认就是规则模式，不会因为缺 key 崩。
   配了凭据才走 `/chat/completions`；模型返回的不是合法 JSON 时，降级成
   `invalid_json` 的 rejected，不抛异常给调用方。
4. **默认传输是 curl 子进程**：真实服务网关按客户端指纹拦截，同一请求体 urllib 拿到
   HTTP 403 / `error code 1010`、curl 拿到 HTTP 200（2026-10-24 实测）。`_real_transport()`
   优先 curl，curl 不存在时明确回落 urllib；两条路径都只发一次请求、不自动重试
   （重复计费比一次失败更糟），非 2xx 一律 `provider_error`，绝不伪造空断言成功。
   请求头（含 `Authorization`）经 stdin 传入 curl，不进 argv；异常与报告里的 key 会被
   抹成 `***`。

能力边界（别把闸门当万灵药）
--------------------------
- **只验坐标，不验语义**：`verify_evidence()` 只比较 `text[char_start:char_end]` 与
  `evidence_quote`。主语指错人、取值张冠李戴、attribute 槽位名与引文无关，本模块一律
  放行——这些要靠服务端断言复用比较与 `backend/moyu_consistency.py` 的一致性检查兜。
- **不做实体链接 / 指代消解 / 别名合并**：规则模式用「前 30 字内出现过全名」当主语闸门，
  跨段回指（「他」「那妇人」）直接放弃该条。
- **不保证召回**：规则只在六个固定模式命中且主语已知时才产出；古典长篇里大量人物
  只以绰号或代称出现，抽不到是设计选择（宁可漏，不可错）。
- **真实 LLM 的召回率与语义幻觉率未在本仓库做规模化验收**：LLM 分支验证了协议、解析、
  降级、传输与确定性；真实服务的连通性由服务侧冒烟负责，本模块只保证「发得出去、错得
  明白」。`NaN`/`Infinity` 这类非法数值已在 `validate_assertion()` 被拒（原因码
  `non_finite`），严格 JSON 由 `strict_json()` 保证。

schema（严格）
-------------
每条断言恰好八个键：`subject` / `predicate` / `attribute` / `value` /
`evidence_quote` / `char_start` / `char_end` / `confidence`。多键、缺键、类型不符、
空串、区间非法、谓词不在白名单、非有限数值（`NaN` / `Infinity` / `-Infinity`）——全部
rejected，不做「尽力修补」。谓词白名单与
`backend/server.py` 的 `ASSERTION_PREDICATES` 保持一致（那边是落库的唯一出口），
这里**不 import server**：抽取器要能在没有数据库、没有服务的机器上单跑。
"""
from __future__ import annotations

import functools
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable, Mapping, Sequence

MODULE_VERSION = "1.0"

#: 断言的八个键，顺序即文档顺序；未知键/缺键都拒绝。
ASSERTION_KEYS: tuple[str, ...] = (
    "subject",
    "predicate",
    "attribute",
    "value",
    "evidence_quote",
    "char_start",
    "char_end",
    "confidence",
)

#: 谓词白名单：与 `backend/server.py` 的 `ASSERTION_PREDICATES` 对齐。
PREDICATES: frozenset[str] = frozenset({"has_story_fact", "has_attribute"})

#: rejected 的原因码。报告按这个顺序输出，便于逐次比对。
REJECT_REASONS: tuple[str, ...] = (
    "unknown_key",
    "missing_key",
    "type_error",
    "non_finite",
    "empty_value",
    "predicate_not_allowed",
    "bad_range",
    "quote_mismatch",
    "duplicate",
    "provider_error",
    "invalid_json",
)

#: 证据引文上限：一句一行的引文足够定位，超长引文通常是模型把整段抄回来。
MAX_QUOTE_CHARS = 240

EMPTY_TEXT_REASON = "empty_text"

Provider = Callable[[str, str], Any]


class Rejected(ValueError):
    """单条断言被拒。`reason` 取自 :data:`REJECT_REASONS`，便于分类计数。"""

    def __init__(self, reason: str, detail: str, payload: Any = None) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.payload = payload


class ExtractProviderError(RuntimeError):
    """provider 调用失败。`reason` 为 `provider_error` 或 `invalid_json`。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------- 数值与类型工具


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def chinese_number(text: str) -> int | None:
    """把「二十五」「一百单八」「12」这类写法转成整数；转不动返回 None。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total, section, seen = 0, 0, False
    for char in text:
        if char in _CN_DIGITS:
            section = _CN_DIGITS[char]
            seen = True
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            section = (section or 1) * unit
            total += section
            section, seen = 0, True
        elif char == "单":
            section = section or 1
            seen = True
        else:
            return None
    return total + section if seen else None


# ---------------------------------------------------------------- schema 校验


def _require_str(payload: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise Rejected("type_error", f"{key} 必须是字符串，实际 {type(value).__name__}", payload)
    if not allow_empty and not value.strip():
        raise Rejected("empty_value", f"{key} 不能为空", payload)
    return value


def _require_span(payload: Mapping[str, Any], key: str) -> int:
    value = payload[key]
    if not _is_int(value):
        raise Rejected("type_error", f"{key} 必须是整数，实际 {type(value).__name__}", payload)
    return value


def validate_assertion(payload: Any) -> dict[str, Any]:
    """严格校验一条原始断言，返回规范化副本；不合规抛 :class:`Rejected`。

    这里只做 schema 与语义边界（谓词白名单、区间形状），**逐字证据比对在**
    :func:`verify_evidence`：那个需要正文，校验本身不需要。
    """
    if not isinstance(payload, Mapping):
        raise Rejected("type_error", f"断言必须是对象，实际 {type(payload).__name__}", payload)

    keys = set(payload)
    unknown = sorted(keys - set(ASSERTION_KEYS))
    if unknown:
        raise Rejected("unknown_key", f"未知键：{', '.join(unknown)}", payload)
    missing = [key for key in ASSERTION_KEYS if key not in keys]
    if missing:
        raise Rejected("missing_key", f"缺少键：{', '.join(missing)}", payload)

    subject = _require_str(payload, "subject")
    predicate = _require_str(payload, "predicate")
    if predicate not in PREDICATES:
        raise Rejected("predicate_not_allowed", f"谓词不在白名单：{predicate}", payload)

    attribute = payload["attribute"]
    if attribute is not None:
        if not isinstance(attribute, str):
            raise Rejected("type_error", "attribute 必须是字符串或 None", payload)
        attribute = attribute.strip()
        if not attribute:
            raise Rejected("empty_value", "attribute 不能是空白串（无槽位请用 None）", payload)
    if predicate == "has_attribute" and attribute is None:
        raise Rejected("empty_value", "has_attribute 必须带 attribute 槽位名", payload)

    value = payload["value"]
    if not isinstance(value, (str, int, float, bool)):
        raise Rejected("type_error", "value 只能是字符串、数字或布尔", payload)
    if isinstance(value, float) and not math.isfinite(value):
        # NaN / Infinity / -Infinity：JSON 严格消费方解析不了，而且 NaN != NaN 会让
        # 去重、比较、digest 全部失真——不许进库，也不许进报告。
        raise Rejected("non_finite", f"value 不是有限数值（{value!r}），拒绝入库", payload)
    if isinstance(value, str) and not value.strip():
        raise Rejected("empty_value", "value 不能为空字符串", payload)

    quote = _require_str(payload, "evidence_quote")
    if len(quote) > MAX_QUOTE_CHARS:
        raise Rejected("bad_range", f"evidence_quote 过长（{len(quote)} > {MAX_QUOTE_CHARS}）", payload)

    char_start = _require_span(payload, "char_start")
    char_end = _require_span(payload, "char_end")
    if char_start < 0 or char_end <= char_start:
        raise Rejected("bad_range", f"区间非法：[{char_start}, {char_end})", payload)
    if char_end - char_start != len(quote):
        raise Rejected("bad_range", f"区间长度 {char_end - char_start} 与引文长度 {len(quote)} 不符", payload)

    confidence = payload["confidence"]
    if not _is_number(confidence):
        raise Rejected("type_error", "confidence 必须是数字", payload)
    if isinstance(confidence, float) and not math.isfinite(confidence):
        raise Rejected("non_finite", f"confidence 不是有限数值（{confidence!r}）", payload)
    if not 0.0 <= float(confidence) <= 1.0:
        raise Rejected("bad_range", "confidence 必须落在 0 到 1 之间", payload)

    return {
        "subject": subject.strip(),
        "predicate": predicate,
        "attribute": attribute,
        "value": value,
        "evidence_quote": quote,
        "char_start": char_start,
        "char_end": char_end,
        "confidence": float(confidence),
    }


def verify_evidence(text: str, assertion: Mapping[str, Any]) -> None:
    """逐字证据校验：`text[char_start:char_end] == evidence_quote`，否则抛 Rejected。"""
    quote = assertion["evidence_quote"]
    start = assertion["char_start"]
    end = assertion["char_end"]
    if start < 0 or end > len(text) or end <= start:
        raise Rejected("bad_range", f"区间 [{start}, {end}) 超出正文长度 {len(text)}", assertion)
    actual = text[start:end]
    if actual != quote:
        raise Rejected("quote_mismatch", f"原文切出 {actual!r}，断言声称 {quote!r}", assertion)


# ---------------------------------------------------------------- 规则抽取（dry-run）


_NAME_RE = re.compile(
    r"姓(?P<surname>[\u4e00-\u9fa5]{1,2})[，,\s]{0,2}(?:名|讳)"
    r"(?P<given>[\u4e00-\u9fa5]{1,2}?)(?=[，,、。；;：:\s]|字|表字|$)"
)
_AGE_RE = re.compile(r"年(?:方|仅|已)?(?P<age>[零〇一二两三四五六七八九十百千单\d]{1,6})岁")
_HEIGHT_RE = re.compile(r"身长(?P<height>[零〇一二两三四五六七八九十百千\d]{1,6})尺")
_CALL_RE = re.compile(
    r"(?:名唤|名曰|名叫|叫做)(?P<given>[\u4e00-\u9fa5]{1,3}?)(?=[，,、。；;：:\s]|$)"
)
_COURTESY_RE = re.compile(
    r"(?:表字|字)(?P<courtesy>[\u4e00-\u9fa5]{1,2}?)(?=[，,、。；;：:\s]|$)"
)
_ORIGIN_RE = re.compile(r"(?:乃|系|本贯|祖贯)(?P<place>[\u4e00-\u9fa5]{2,4}?)(?:人氏|人也|人士)")


def _rule_name(text: str) -> Iterable[dict[str, Any]]:
    for match in _NAME_RE.finditer(text):
        surname = match.group("surname")
        given = match.group("given")
        name = f"{surname}{given}"
        yield {
            "subject": name,
            "predicate": "has_attribute",
            "attribute": "real_name",
            "value": name,
            "evidence_quote": match.group(0),
            "char_start": match.start(),
            "char_end": match.end(),
            "confidence": 0.9,
        }


def _rule_age(text: str) -> Iterable[dict[str, Any]]:
    for match in _AGE_RE.finditer(text):
        age = chinese_number(match.group("age"))
        if age is None or not 1 <= age <= 120:
            continue
        # 主语取引文前的一小段里最近的人名样式片段；取不到就不猜，跳过（精度优先）。
        subject = _nearest_subject(text, match.start())
        if not subject:
            continue
        yield {
            "subject": subject,
            "predicate": "has_attribute",
            "attribute": "age",
            "value": age,
            "evidence_quote": match.group(0),
            "char_start": match.start(),
            "char_end": match.end(),
            "confidence": 0.85,
        }


def _rule_height(text: str) -> Iterable[dict[str, Any]]:
    for match in _HEIGHT_RE.finditer(text):
        height = chinese_number(match.group("height"))
        if height is None or not 1 <= height <= 20:
            continue
        subject = _nearest_subject(text, match.start())
        if not subject:
            continue
        yield {
            "subject": subject,
            "predicate": "has_attribute",
            "attribute": "height",
            "value": height,
            "evidence_quote": match.group(0),
            "char_start": match.start(),
            "char_end": match.end(),
            "confidence": 0.8,
        }


_SUBJECT_WINDOW = 30


def _nearest_subject(text: str, position: int) -> str:
    """在命中位置前 30 字里找「姓X名Y」得到的人名，找不到返回空串。

    只认已经出现过全名的角色——这是精度闸门：给不出主语时宁可不抽，也不拿
    「他」「那人」当 subject 灌进库。
    """
    window_start = max(0, position - _SUBJECT_WINDOW)
    window = text[window_start:position]
    found = ""
    for match in _NAME_RE.finditer(window):
        found = f"{match.group('surname')}{match.group('given')}"
    return found


def _gated(text: str, match: re.Match[str], *, attribute: str, value: Any,
           confidence: float) -> dict[str, Any] | None:
    """有主语才成立的一条断言；主语取不到返回 None（宁缺勿滥）。"""
    subject = _nearest_subject(text, match.start())
    if not subject:
        return None
    return {
        "subject": subject,
        "predicate": "has_attribute",
        "attribute": attribute,
        "value": value,
        "evidence_quote": match.group(0),
        "char_start": match.start(),
        "char_end": match.end(),
        "confidence": confidence,
    }


def _rule_called_name(text: str) -> Iterable[dict[str, Any]]:
    """`名唤X` 这类：只在同段前方出现过全名时采用。"""
    for match in _CALL_RE.finditer(text):
        item = _gated(text, match, attribute="real_name", value=match.group("given"), confidence=0.75)
        if item:
            yield item


def _rule_courtesy_name(text: str) -> Iterable[dict[str, Any]]:
    """`字X` / `表字X`，同样要求主语已知。"""
    for match in _COURTESY_RE.finditer(text):
        item = _gated(text, match, attribute="courtesy_name", value=match.group("courtesy"), confidence=0.7)
        if item:
            yield item


def _rule_origin(text: str) -> Iterable[dict[str, Any]]:
    """`乃X人氏` / `系X人` 这类籍贯写法，要求主语已知。"""
    for match in _ORIGIN_RE.finditer(text):
        item = _gated(text, match, attribute="hometown", value=match.group("place"), confidence=0.7)
        if item:
            yield item


RULES: tuple[Callable[[str], Iterable[dict[str, Any]]], ...] = (
    _rule_name,
    _rule_called_name,
    _rule_courtesy_name,
    _rule_origin,
    _rule_age,
    _rule_height,
)


def dry_run_provider(text: str, chapter_id: str) -> list[dict[str, Any]]:
    """确定性规则抽取（默认 provider）。同样的正文永远得到同样的顺序与内容。"""
    collected: list[dict[str, Any]] = []
    for rule in RULES:
        collected.extend(rule(text))
    return collected


# ---------------------------------------------------------------- OpenAI 兼容 provider


#: 单次请求的超时（秒）。真实模型单章实测 30–50 秒、长尾超过 120 秒
#: （2026-10-24 真实服务采样），30 秒会把 reasoning 模型的正常输出误判成超时，
#: 因此默认放宽到 180 秒；调用方可以用参数或 `MOYU_EXTRACT_TIMEOUT` 覆盖。
DEFAULT_TIMEOUT = 180.0
#: 允许的超时区间（秒），越界一律 ValueError，不做静默夹取。
TIMEOUT_LIMITS = (1.0, 3600.0)
#: 覆盖超时的环境变量（只在有凭据时才读；纯离线模式完全不碰它）。
ENV_TIMEOUT = "MOYU_EXTRACT_TIMEOUT"
#: curl 自身的硬杀时间 = 请求超时 + 这个余量，避免 curl 比进程内计时器先死/后死不一致。
CURL_TIMEOUT_MARGIN = 15.0

PROMPT_TEMPLATE = """你在从中文小说正文里抽取事实断言。只输出 JSON，不要解释。

要求：
1. 每条断言恰好这些键：subject, predicate, attribute, value, evidence_quote,
   char_start, char_end, confidence。
2. predicate 只能是 has_story_fact 或 has_attribute；用 has_attribute 时必须给
   attribute（槽位名，例如 age / height / real_name）。
3. evidence_quote 必须是正文里的**原文片段**，char_start / char_end 是它在正文中的
   字符下标（Python 切片语义，左闭右开），且 char_end - char_start 必须等于引文长度。
   引文对不上就整条不要输出——下游会逐字校验并丢弃。
4. 抽不到就输出 {"assertions": []}。

正文（章节 {chapter_id}）：
"""


class OpenAIProvider:
    """OpenAI 兼容 `/chat/completions` 的抽取 provider。

    默认传输是 **curl 子进程**（`_real_transport`）：真实服务网关按客户端指纹拦截，
    同一请求体 urllib 拿到 HTTP 403 / `error code 1010`，curl 拿到 HTTP 200。
    curl 不存在时明确回落到 urllib，行为与旧版一致。

    `transport(url, headers, body)` 可注入，便于离线测试；注入后完全不碰默认传输。
    任何路径都**只发一次请求**，不做自动重试（重复计费比一次失败更糟）。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Callable[[str, Mapping[str, str], bytes], tuple[int, str]] | None = None,
        max_chars: int = 12000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = _require_timeout(timeout)
        self.transport = transport
        self.max_chars = max_chars

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def build_request(self, text: str, chapter_id: str) -> tuple[str, dict[str, str], bytes]:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": "你是严格的结构化信息抽取器，只输出 JSON。"},
                    {"role": "user", "content": PROMPT_TEMPLATE.replace("{chapter_id}", chapter_id)
                     + text[: self.max_chars]},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        return self.endpoint, headers, body

    def _redact_detail(self, payload: Any, headers: Mapping[str, str]) -> str:
        """把要进 detail 的响应体先按凭据全形态脱敏，截断由调用方做。

        t68 F1/F2 的根因就是「先截断后脱敏」：凭据跨过 200 字符边界时，截断留下的
        前半段里还带着 key 的可识别前缀，再脱敏也认不出它了。这里的顺序不可交换。
        """
        secrets = list(_header_secrets(headers))
        if self.api_key and self.api_key not in secrets:
            secrets.append(self.api_key)
        return _redact_secrets(str(payload), secrets)

    def __call__(self, text: str, chapter_id: str) -> list[dict[str, Any]]:
        url, headers, body = self.build_request(text, chapter_id)
        transport = self.transport or functools.partial(_real_transport, timeout=self.timeout)
        try:
            status, payload = transport(url, headers, body)
        except ExtractProviderError as error:
            # 传输层已经分类过（provider_error / invalid_json），这里只补一层脱敏：
            # 无论异常来自哪条路径，API Key 都不能出现在 detail 里。
            raise ExtractProviderError(error.reason, _redact(error.detail, self.api_key)) from error
        except Exception as error:  # noqa: BLE001 - 网络异常一律降级，不抛给调用方
            raise ExtractProviderError(
                "provider_error", _redact(f"请求失败：{error}", self.api_key)
            ) from error
        if status < 200 or status >= 300:
            # 先完整脱敏、再截断：状态码与网关错误信息（如 403 + error code 1010）保持可见。
            safe_payload = self._redact_detail(payload, headers)
            raise ExtractProviderError("provider_error", f"HTTP {status}：{safe_payload[:200]}")
        try:
            envelope = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ExtractProviderError("invalid_json", f"响应不是 JSON：{error}") from error
        content = None
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ExtractProviderError("invalid_json", "响应缺少 choices[0].message.content")
        if not isinstance(content, str):
            raise ExtractProviderError("invalid_json", "content 不是字符串")
        parsed = _parse_content(content)
        if parsed is None:
            # 同一顺序要求：content 也可能原样回显请求头里的凭据。
            safe_content = self._redact_detail(content, headers)
            raise ExtractProviderError(
                "invalid_json", f"content 不是可解析的断言 JSON：{safe_content[:200]}"
            )
        return parsed


def _parse_content(content: str) -> list[dict[str, Any]] | None:
    """从模型 content 里取出断言数组；容忍 ```json 围栏，其余一律当失败。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        parsed = parsed.get("assertions")
    if not isinstance(parsed, list):
        return None
    return [item for item in parsed]


#: 凭据形态识别（形状驱动，不依赖具体 key）：报告层用它们把 detail 里的原值抹掉。
_CREDENTIAL_NAME = (
    r"(?:api[_-]?key|apikey|access[_-]?key|secret(?:[_-]?key)?|token|password|passwd"
    r"|auth[_-]?key|x-api-key|x-auth-token|credential[s]?|session[_-]?id"
    r"|private[_-]?key|cookie|set-cookie)"
)
# 「名字 = 值」/「名字: 值」：保留名字（诊断可读），只把值换掉。
# `Authorization` 的值是「scheme + 令牌」两段：必须整段抹，否则先抹掉 scheme 后令牌会裸奔。
_AUTHORIZATION_RE = re.compile(
    r"(?i)\b(authorization\b\s*[:=]\s*)(?:[a-z]{3,12}\s+)?[A-Za-z0-9._~+/=\-][^\s,;；，、：]*"
)
# `Authorization: Digest username="u", response="<摘要>"` 的参数值也是凭据片段。
_DIGEST_PARAM_RE = re.compile(
    r'(?i)\b(response|nonce|cnonce|opaque)\s*=\s*(?:"[^"]*"|[^\s,;；，、：]+)'
)
# 没有 header 名、只剩 scheme 的裸令牌（Bearer/Basic/Token/Digest；Basic 需 >=6 位，避开 basic plan 这类短语）。
_SCHEME_TOKEN_RE = re.compile(
    r"(?i)\b(bearer|basic|token|digest)\s+[A-Za-z0-9._~+/=\-]{6,}"
)
_CREDENTIAL_PAIR_RE = re.compile(
    r"(?i)\b(" + _CREDENTIAL_NAME + r"\b\s*[:=]\s*)[^\s,;；，、：]+"
)
# 常见令牌前缀：sk-/pk-/ghp_/xox*-/AKIA… 等，含 AWS 访问密钥 ID。
_TOKEN_PREFIX_RE = re.compile(
    r"\b(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[abprs]|AKIA|ASIA)[-_A-Za-z0-9]{4,}\b"
)
# 无前缀的高熵串兜底（>=32 位连续字母数字下划线连字符）。
_HIGH_ENTROPY_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def _mask_credentials(text: str) -> str:
    """报告层兜底脱敏：把文本里**像凭据的片段**抹掉，不要求调用方知道具体 key。

    自定义 provider 抛异常时，调用栈里往往带着 `Authorization: Bearer sk-...`、
    `api_key=...`、`x-api-key: ...`、AWS `AKIA...`；报告要落盘，所以按**形状**识别：
    名字像凭据的键值对、常见令牌前缀、无前缀高熵串，一律换成 `***`（t71 / t73）。

    刻意保留诊断信息：HTTP 状态码、`error code 1010`、`bad_gateway` 这类短标识不受影响。
    """
    masked = str(text)
    # 1) Authorization 整段（scheme + 令牌）先抹：顺序颠倒会把 scheme 吃掉、令牌裸奔
    masked = _AUTHORIZATION_RE.sub(lambda m: m.group(1) + "***", masked)
    # 2) Digest 参数值（response/nonce/cnonce/opaque）
    masked = _DIGEST_PARAM_RE.sub(lambda m: m.group(1) + '="***"', masked)
    # 3) 没有 header 名的裸 scheme 令牌（Bearer/Token/Digest）
    masked = _SCHEME_TOKEN_RE.sub(lambda m: m.group(1) + " ***", masked)
    # 4) 键值对：保留键名，抹掉值（覆盖 api_key=… / token: … / password=… 等无前缀形态）
    masked = _CREDENTIAL_PAIR_RE.sub(lambda m: m.group(1) + "***", masked)
    # 5) 常见令牌前缀（含 AWS AKIA/ASIA）
    masked = _TOKEN_PREFIX_RE.sub("***", masked)
    # 6) 无前缀高熵串兜底
    masked = _HIGH_ENTROPY_RE.sub("***", masked)
    return masked

def _redact(text: str, secret: str) -> str:
    """把凭据从任何要进入异常/日志/报告的文本里抹掉。

    唯一的目的：错误信息可以带 URL、状态码、响应体片段，但**不带 API Key**。
    """
    if not secret:
        return text
    return text.replace(secret, "***")


MIN_SECRET_CHARS = 3
"""通用 header 取值的最小脱敏长度；凭据头（Authorization）不受此限。"""


def _redact_secrets(text: str, secrets: Iterable[str]) -> str:
    """按一组**已经筛选过**的凭据串脱敏（curl 层用：它拿不到 key，只拿得到请求头）。

    替换按长度降序进行——先抹长串，否则短串先吃掉长串的一部分，剩下的片段照样泄漏。
    「哪些值算凭据」由 :func:`_header_secrets` 决定（通用 header 仍是 ≥8 才脱敏，避免
    把 `POST` 这类短通用值误替换；`Authorization` 不设长度门槛）。
    """
    redacted = text
    for secret in sorted(secrets, key=len, reverse=True):
        if isinstance(secret, str) and secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def _header_secrets(headers: Mapping[str, str]) -> list[str]:
    """把请求头里需要脱敏的凭据挑出来。

    分两档：
    - **凭据头**（`Authorization`，名字大小写不敏感）：整值、scheme 之后的剩余值、以及
      值的每一行都无条件登记——key 可能含空白/换行，也可能短于 8 字符，任何长度都不
      放过。只丢长度 < :data:`MIN_SECRET_CHARS` 的碎片，避免把 "a" 这种单字符抹得到处都是。
    - **其他 header**：维持原规则（整值 ≥8、末个空白 token ≥8），不看大小写、不做特殊处理后
      果——`Content-Type: application/json` 这类值不会被当成凭据。
    """
    secrets: set[str] = set()
    for name, value in headers.items():
        if not isinstance(value, str):
            continue
        if isinstance(name, str) and name.strip().lower() == "authorization":
            candidates = [value.strip()]
            remainder = ""
            parts = value.split(None, 1)
            if len(parts) == 2:
                remainder = parts[1].strip()
                candidates.append(remainder)
            for text_part in (value, remainder):
                # 换行 / 空白都可能把 key 切成片段：整行与每个 token 都登记，
                # 这样「只回显裸 key 前半段」也抹得掉。
                candidates.extend(line.strip() for line in text_part.splitlines())
                candidates.extend(token for token in text_part.split())
            for candidate in candidates:
                if len(candidate) >= MIN_SECRET_CHARS:
                    secrets.add(candidate)
            continue
        if len(value) >= 8:
            secrets.add(value)
        parts = value.split()
        if parts and len(parts[-1]) >= 8:
            secrets.add(parts[-1])
    return sorted(secrets, key=len, reverse=True)


def _require_timeout(value: Any, source: str = "timeout") -> float:
    """校验超时秒数；非法值显式报错，不静默回退默认值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{source} 必须是数字秒数（1 到 3600 之间）。")
    seconds = float(value)
    if not math.isfinite(seconds) or not (TIMEOUT_LIMITS[0] <= seconds <= TIMEOUT_LIMITS[1]):
        raise ValueError(f"{source} 必须是 1 到 3600 之间的有限秒数，收到 {value!r}。")
    return seconds


def _urllib_transport(
    url: str, headers: Mapping[str, str], body: bytes, *, timeout: float = DEFAULT_TIMEOUT
) -> tuple[int, str]:
    """标准库传输。被部分网关按 TLS/HTTP2 指纹拦截（实测 403 / error code 1010），只作回落。"""
    seconds = _require_timeout(timeout)
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=seconds) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:  # 4xx/5xx：把响应体带回去做证据
        return error.code, error.read().decode("utf-8", "replace")


def curl_available(curl_bin: str = "curl") -> bool:
    """curl 是否可用（决定默认传输走子进程还是回落 urllib）。"""
    return shutil.which(curl_bin) is not None


def _curl_config(
    url: str, headers: Mapping[str, str], body_path: str, output_path: str, timeout: float
) -> str:
    """生成 curl 的 `--config` 文本。

    请求头（含 `Authorization`）**只走 stdin 的配置文件**，不进 argv，
    这样 `ps` 看不到 API Key；请求体写临时文件，同样不进 argv。
    """
    lines = [
        f"url = {json.dumps(url)}",
        'request = "POST"',
        "silent",
        "show-error",
        f"max-time = {timeout:g}",
        f"data-binary = {json.dumps('@' + body_path)}",
        f"output = {json.dumps(output_path)}",
        'write-out = "%{http_code}"',
    ]
    for name, value in headers.items():
        lines.append(f"header = {json.dumps(f'{name}: {value}')}")
    return "\n".join(lines) + "\n"


def _subprocess_runner(cmd: Sequence[str], payload: bytes, timeout: float) -> tuple[int, str, str]:
    """默认 runner：真正拉起 curl。测试可注入同名签名的假 runner。"""
    completed = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout)
    return (
        completed.returncode,
        completed.stdout.decode("utf-8", "replace"),
        completed.stderr.decode("utf-8", "replace"),
    )


def _curl_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    curl_bin: str = "curl",
    runner: Callable[[Sequence[str], bytes, float], tuple[int, str, str]] | None = None,
) -> tuple[int, str]:
    """用 curl 子进程发请求。

    为什么不是 urllib：真实服务网关按客户端指纹拦截，同一请求体 urllib 拿到
    HTTP 403 / `error code 1010`，curl 拿到 HTTP 200（2026-10-24 实测）。
    这里只换传输层，**不改判定语义**：非 2xx 仍原样返回状态码，由 provider 转成
    `provider_error`；不是 2xx 也绝不伪造空断言成功。
    """
    seconds = _require_timeout(timeout)
    run = runner or _subprocess_runner
    workspace = tempfile.mkdtemp(prefix="moyu-extract-")
    body_path = os.path.join(workspace, "request.json")
    output_path = os.path.join(workspace, "response.txt")
    try:
        with open(body_path, "wb") as handle:
            handle.write(body)
        config = _curl_config(url, headers, body_path, output_path, seconds)
        try:
            code, stdout, stderr = run(
                [curl_bin, "--config", "-"], config.encode("utf-8"), seconds + CURL_TIMEOUT_MARGIN
            )
        except FileNotFoundError as error:
            raise ExtractProviderError(
                "provider_error", f"curl 不可用（{curl_bin}），无法发送请求：{error}"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ExtractProviderError(
                "provider_error",
                f"curl 超时：超过 {seconds + CURL_TIMEOUT_MARGIN:g} 秒被强制终止（请求超时设定 {seconds:g} 秒）",
            ) from error
        if code != 0:
            reason = "超时" if code == 28 else "失败"
            # 顺序很重要：**先对完整 stderr 脱敏，再截断**。反过来时，凭据一旦跨过
            # 200 字符边界就只剩前半截留在 detail 里，脱敏匹配不上，等于泄漏前缀
            # （t63 复审 F1）。
            safe_stderr = _redact_secrets(stderr.strip(), _header_secrets(headers))
            detail = f"curl {reason}（退出码 {code}，请求超时设定 {seconds:g} 秒）：{safe_stderr[:200]}"
            raise ExtractProviderError("provider_error", detail)
        status_text = stdout.strip()
        if not status_text.isdigit():
            safe_status = _redact_secrets(status_text, _header_secrets(headers))
            raise ExtractProviderError(
                "provider_error", f"curl 未返回 HTTP 状态码：{safe_status[:120]!r}"
            )
        payload = ""
        if os.path.exists(output_path):
            with open(output_path, "rb") as handle:
                payload = handle.read().decode("utf-8", "replace")
        return int(status_text), payload
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _real_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    curl_bin: str = "curl",
    curl_runner: Callable[[Sequence[str], bytes, float], tuple[int, str, str]] | None = None,
) -> tuple[int, str]:
    """默认真实传输：curl 优先（可移植），curl 缺失时**明确回落** urllib。

    两条路径都只发一次请求；失败原样抛出，不做自动重试（避免重复计费）。
    """
    if curl_available(curl_bin):
        return _curl_transport(
            url, headers, body, timeout=timeout, curl_bin=curl_bin, runner=curl_runner
        )
    return _urllib_transport(url, headers, body, timeout=timeout)


ENV_KEYS = ("MOYU_EXTRACT_BASE_URL", "MOYU_EXTRACT_MODEL", "MOYU_EXTRACT_API_KEY")


def provider_from_env(environ: Mapping[str, str] | None = None) -> OpenAIProvider | None:
    """按环境变量构造 provider；缺任意一项返回 None（= 离线规则模式），不抛异常。

    凭据齐全时才会读 `MOYU_EXTRACT_TIMEOUT`：非法值显式 ValueError（配置错了就别
    假装用默认值跑），合法值覆盖 `DEFAULT_TIMEOUT`。
    """
    env = os.environ if environ is None else environ
    values = [str(env.get(key, "")).strip() for key in ENV_KEYS]
    if not all(values):
        return None
    base_url, model, api_key = values
    raw_timeout = str(env.get(ENV_TIMEOUT, "")).strip()
    timeout = DEFAULT_TIMEOUT
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
        except ValueError:
            raise ValueError(
                f"{ENV_TIMEOUT} 必须是数字秒数（1 到 3600 之间），收到 {raw_timeout!r}。"
            ) from None
        timeout = _require_timeout(timeout, ENV_TIMEOUT)
    return OpenAIProvider(base_url, model, api_key, timeout=timeout)


def resolve_provider(provider: Any = None, environ: Mapping[str, str] | None = None) -> tuple[Any, str]:
    """把 `provider` 参数归一成 (callable, 模式名)。

    - 显式传了 callable → 用它，模式 `custom`
    - 显式传了 `"dry-run"` / `"rule"` → 规则模式
    - 传 `"openai"` 或没传但有凭据 → OpenAIProvider
    - 其余 → 规则模式（离线默认）
    """
    if callable(provider):
        return provider, "custom"
    if provider in ("dry-run", "dry_run", "rule"):
        return dry_run_provider, "dry-run"
    configured = provider_from_env(environ)
    if provider in ("openai", "llm") and configured is None:
        return dry_run_provider, "dry-run"
    if configured is not None:
        return configured, "openai"
    return dry_run_provider, "dry-run"


# ---------------------------------------------------------------- 对外接口


def strict_json(payload: Any) -> str:
    """严格 JSON 序列化：遇到 NaN / Infinity 直接报错，绝不输出 `NaN` 这种非法字面量。

    `json.dumps` 默认 `allow_nan=True`，会把 `float("nan")` 写成裸 `NaN`——Python 自己
    能读回来，但任何严格 JSON 消费方（浏览器 `JSON.parse`、Go、Rust、Postgres jsonb）
    都会解析失败。这里把开关关掉：宁可在这里抛 `ValueError`，也不产出一份「只有 Python
    认得」的 JSON。
    """
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False, default=str)
    except ValueError as error:
        raise ValueError(f"strict_json 拒绝非有限数值或不可序列化对象：{error}") from error


def _sort_key(assertion: Mapping[str, Any]) -> tuple[Any, ...]:
    value = assertion["value"]
    return (
        assertion["char_start"],
        assertion["char_end"],
        assertion["subject"],
        assertion["predicate"],
        assertion["attribute"] or "",
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False),
    )


def extract_chapter_report(
    text: str,
    *,
    chapter_id: str,
    provider: Any = None,
    environ: Mapping[str, str] | None = None,
    max_assertions: int = 500,
) -> dict[str, Any]:
    """抽取一章，返回断言 + rejected 明细 + 原因分布 + 模式（报告用）。

    从不抛异常给调用方：provider 挂了、模型吐了非法 JSON，都变成 rejected。
    """
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串（章节正文）")
    if not isinstance(chapter_id, str) or not chapter_id.strip():
        raise TypeError("chapter_id 必须是非空字符串")

    callable_provider, mode = resolve_provider(provider, environ)
    rejected: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}

    def _reject(reason: str, detail: str, payload: Any = None) -> None:
        rejected.append({"reason": reason, "detail": detail,
                         "payload": payload if isinstance(payload, (dict, list, str, int, float, bool, type(None))) else None})
        reasons[reason] = reasons.get(reason, 0) + 1

    if not text.strip():
        _reject(EMPTY_TEXT_REASON, "章节正文为空")
        return {"chapter_id": chapter_id, "mode": mode, "characters": len(text),
                "assertions": [], "rejected": rejected, "rejected_reasons": reasons}

    try:
        raw = callable_provider(text, chapter_id)
    except ExtractProviderError as error:
        # provider 层已脱敏过；这里再兜一层，防止自定义 provider 直接把凭据塞进 detail。
        _reject(error.reason, _mask_credentials(error.detail))
        raw = []
    except Exception as error:  # noqa: BLE001 - 自定义 provider 崩了也不许拖垮整章
        # 非契约异常：原文可能带 Authorization/API key，只保留异常类型（可诊断），不写原文。
        _reject(
            "provider_error",
            f"provider 调用失败：{type(error).__name__}（异常原文已隐去，避免凭据写入报告）",
        )
        raw = []

    if isinstance(raw, Mapping):
        # provider 可能直接给 `{"assertions": [...]}` 信封，也可能只给一条断言对象。
        raw = raw["assertions"] if "assertions" in raw else [dict(raw)]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        _reject("invalid_json", f"provider 返回的不是数组：{type(raw).__name__}")
        raw = []

    accepted: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in raw:
        if len(accepted) >= max_assertions:
            _reject("duplicate", f"超过单章上限 {max_assertions} 条，其余丢弃")
            break
        try:
            assertion = validate_assertion(item)
            verify_evidence(text, assertion)
        except Rejected as error:
            _reject(error.reason, error.detail, error.payload)
            continue
        key = (assertion["subject"], assertion["predicate"], assertion["attribute"],
               strict_json(assertion["value"]),
               assertion["char_start"], assertion["char_end"])
        if key in seen:
            _reject("duplicate", "同一实体/槽位/取值/区间重复出现", assertion)
            continue
        seen.add(key)
        accepted.append(assertion)

    accepted.sort(key=_sort_key)
    return {
        "chapter_id": chapter_id,
        "mode": mode,
        "characters": len(text),
        "assertions": accepted,
        "rejected": rejected,
        "rejected_reasons": reasons,
    }


def extract_chapter(text: str, *, chapter_id: str, provider: Any = None,
                    environ: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """抽取一章并返回断言列表（按 char_start 排序，逐字节确定）。

    `provider` 可以是 callable（`provider(text, chapter_id) -> list`）、`"dry-run"`
    或 `"openai"`；不传时看环境变量，没凭据就是确定性的规则模式。
    """
    report = extract_chapter_report(text, chapter_id=chapter_id, provider=provider, environ=environ)
    return report["assertions"]


def digest(assertions: Sequence[Mapping[str, Any]]) -> str:
    """断言列表的稳定摘要（同一输入两次调用必须一致，fixture 用它做确定性断言）。

    走 :func:`strict_json`：含 NaN / Infinity 的断言列表会在这里抛 `ValueError`，
    而不是产出一个「摘要对得上、但 JSON 解析不了」的假确定性。
    """
    payload = strict_json(list(assertions))
    return f"sha256:{__import__('hashlib').sha256(payload.encode('utf-8')).hexdigest()}"


__all__ = [
    "ASSERTION_KEYS",
    "DEFAULT_TIMEOUT",
    "EMPTY_TEXT_REASON",
    "ENV_KEYS",
    "ENV_TIMEOUT",
    "ExtractProviderError",
    "MAX_QUOTE_CHARS",
    "MODULE_VERSION",
    "OpenAIProvider",
    "PREDICATES",
    "REJECT_REASONS",
    "Rejected",
    "TIMEOUT_LIMITS",
    "chinese_number",
    "curl_available",
    "digest",
    "dry_run_provider",
    "extract_chapter",
    "extract_chapter_report",
    "provider_from_env",
    "resolve_provider",
    "strict_json",
    "validate_assertion",
    "verify_evidence",
]
