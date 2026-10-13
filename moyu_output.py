"""Shared mechanical checks for author-facing prose output.

The output contract is deliberately narrow.  It checks only the parts that
can be established without judging literary quality: an explicitly requested
length range, non-whitespace character counting (punctuation included), and
high-confidence planning text accidentally emitted by a model.  It never
rewrites or filters the candidate text.

Callers should pass the text that was actually sent for this request as
``source_text`` (or its already-counted ``source_characters``).  This matters
for polishing a selection: a percentage requirement applies to that selected
range, not to the whole chapter that happened to contain it.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any


COUNTING_RULE = "non_whitespace_characters_including_punctuation"
_BODY_TASKS = frozenset({"continue", "polish"})
_TASK_WORDS = re.compile(r"(?:续写|润色|改写|重写)")
_CONTROL_WORDS = re.compile(
    r"(?:字数|字数控制|篇幅|长度|正文|控制在|控制为|限制为|限字|不少于|至少|"
    r"不低于|最低|不超过|最多|不高于|上限)"
)
_CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
                   "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
_NUMERAL_CHARACTERS = "零〇一二两三四五六七八九十百千万"
_NUMBER = (rf"(?<![\d{_NUMERAL_CHARACTERS}点])[-+负]?"
           rf"[\d{_NUMERAL_CHARACTERS}][\d{_NUMERAL_CHARACTERS}点,.]*")
_CHARACTER_UNIT = r"(?:个?\s*)?(?:汉字|中文字|字符|字)"
_RANGE_SEPARATOR = r"(?:至|到|[-~～])"
_RELATIVE_BASE = r"(?:原文|全文|原稿|所选正文|选中正文|本次正文)(?:的|长度的|字数的)?"


def _percent_pattern(group: str) -> str:
    return (rf"(?:(?=百分之)|(?={_NUMBER}\s*[%％]))"
            rf"(?P<{group}_prefix>百分之)?(?P<{group}>{_NUMBER})\s*"
            rf"(?P<{group}_symbol>[%％])?")

# These forms describe an output requirement, not a number mentioned in story
# material.  The surrounding-clause check below keeps extraction conservative.
_ABSOLUTE_RANGE_RE = re.compile(
    rf"(?P<minimum>{_NUMBER})\s*{_RANGE_SEPARATOR}\s*"
    rf"(?P<maximum>{_NUMBER})\s*{_CHARACTER_UNIT}"
)
_ABSOLUTE_EXACT_RE = re.compile(
    rf"(?P<value>{_NUMBER})\s*{_CHARACTER_UNIT}"
)
_ABSOLUTE_MIN_RE = re.compile(
    rf"(?:不少于|至少|不低于|最低(?:不少于)?|下限(?:为)?)[：:\s]*"
    rf"(?P<value>{_NUMBER})\s*{_CHARACTER_UNIT}"
)
_ABSOLUTE_MAX_RE = re.compile(
    rf"(?:不超过|最多|不高于|上限(?:为)?|上限)[：:\s]*"
    rf"(?P<value>{_NUMBER})\s*{_CHARACTER_UNIT}"
)
_SOFT_TARGET_RE = re.compile(
    rf"(?:^|[。\n！？;；,，])\s*"
    rf"(?:(?:正文(?:长度|字数)?|字数|篇幅|长度)\s*)?"
    rf"目标\s*(?:为|是)?[：:\s]*(?:大约|约)?\s*"
    rf"(?P<value>{_NUMBER})\s*{_CHARACTER_UNIT}"
    rf"(?=$|[\s，,。！？;；])"
)

_RELATIVE_RANGE_RE = re.compile(
    rf"{_RELATIVE_BASE}\s*{_percent_pattern('minimum')}\s*{_RANGE_SEPARATOR}\s*"
    rf"{_percent_pattern('maximum')}"
)
_RELATIVE_MIN_RE = re.compile(
    rf"(?:{_RELATIVE_BASE}\s*(?:不少于|至少|不低于)|"
    rf"(?:不少于|至少|不低于)\s*{_RELATIVE_BASE})\s*{_percent_pattern('value')}"
)
_RELATIVE_MAX_RE = re.compile(
    rf"(?:{_RELATIVE_BASE}\s*(?:不超过|最多|不高于)|"
    rf"(?:不超过|最多|不高于)\s*{_RELATIVE_BASE})\s*{_percent_pattern('value')}"
)
_QUOTED_REQUIREMENT_RE = re.compile(
    r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"(?:\\.|[^"\\])*"|'
    r"'(?:\\.|[^'\\])*'"
)

# Planning markers are intentionally anchored to the beginning of the whole
# output or a line.  A phrase in ordinary prose, especially dialogue, must not
# make a candidate ineligible merely because it contains these words.
_OPEN_TAG_RE = re.compile(
    r"^[ \t]*<\s*(?:think|analysis|reasoning)\s*>",
    re.IGNORECASE | re.MULTILINE,
)
_LINE_PLANNING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:思考过程|推理过程|创作思路|写作计划|分析|构思正文|"
    r"Analysis|Reasoning|Planning)\s*(?:[:：]|$)",
    re.IGNORECASE,
)
_LINE_META_RE = re.compile(
    r"^\s*(?:我们需要(?:回答|创作|撰写|续写|控制字数)|"
    r"用户要求(?:续写|写作|创作))",
)
_META_BOUNDARY = r"(?=[\s，,。！？:：；;]|$)"
_LINE_WRITING_META_RE = re.compile(
    r"^\s*(?:"
    r"我们(?:需要|要|得)(?:先)?分析(?:这个|本次|当前)?"
    rf"(?:写作任务|创作任务|续写任务|润色任务|任务要求|任务){_META_BOUNDARY}|"
    rf"我们得精心写一段{_META_BOUNDARY}|"
    rf"我们要(?:续写|润色|改写){_META_BOUNDARY}|"
    rf"让我(?:先)?构思(?:情节|正文){_META_BOUNDARY}|"
    r"让我(?:先)?构思(?:一个|一段)?(?:约|大约)?\d+\s*字的"
    rf"(?:续写|正文){_META_BOUNDARY}|"
    rf"我要(?:构造|设计|规划)(?:候选正文|续写段落|段落){_META_BOUNDARY}|"
    rf"下面我将(?:生成|输出)(?:续写|润色|改写)后的正文{_META_BOUNDARY}"
    r")",
)
_LINE_ENGLISH_META_RE = re.compile(
    r"^\s*(?:We need to|The user (?:asks|wants|requested))\b",
    re.IGNORECASE,
)
_DIALOGUE_PREFIX = frozenset({
    '"', "'", "“", "”", "‘", "’", "「", "」", "『", "』", "﹁", "﹂",
    "‹", "›", "«", "»", "《", "》", "〈", "〉",
})


def count_characters(text: str) -> int:
    """Count non-whitespace Unicode characters; punctuation is included."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return sum(not character.isspace() for character in text)


# The longer name makes call sites self-documenting while the short alias is
# kept for compatibility with the literary-evaluation helper.
count_non_whitespace_characters = count_characters


def _chinese_section(value: str) -> int:
    # Reject omitted-place abbreviations such as "一百二" instead of guessing.
    if value in _CHINESE_UNITS:
        return _CHINESE_UNITS[value]
    total, previous_unit = 0, 10_000
    digit: int | None = None
    digit_form = ""
    zero = False
    for character in value:
        if character in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[character]
            if digit is not None:
                raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
            if current == 0:
                if not total or zero:
                    raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
                zero = True
            else:
                digit = current
                digit_form = character
            continue
        unit = _CHINESE_UNITS[character]
        if (unit >= previous_unit or zero and unit * 10 == previous_unit
                or previous_unit != 10_000 and unit * 10 < previous_unit and not zero
                or unit == 10 and digit_form == "两"):
            raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
        if digit is None:
            if total or unit != 10:
                raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
            digit = 1
        total += digit * unit
        previous_unit, digit, digit_form, zero = unit, None, "", False
    if digit is None:
        if zero:
            raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
    elif total and (previous_unit != 10 and not zero or digit_form == "两"):
        raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
    return total + (digit or 0)


def _number(value: str) -> int:
    if re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)", value):
        return int(value.replace(",", ""))
    if not value or any(character not in _NUMERAL_CHARACTERS for character in value):
        raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
    if all(character in _CHINESE_DIGITS for character in value):
        if "两" in value and len(value) != 1:
            raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
        return int("".join(str(_CHINESE_DIGITS[character]) for character in value))
    if value.count("万") > 1:
        raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
    if value == "万":
        return 10_000
    if "万" not in value:
        return _chinese_section(value)
    high, low = value.split("万")
    if not high:
        raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
    high_value = _chinese_section(high)
    if not low:
        return high_value * 10_000
    leading_zero = low[0] in {"零", "〇"}
    low_value = _chinese_section(low[1:] if leading_zero else low)
    if not low_value or leading_zero != (low_value < 1000):
        raise ValueError("字数中的数字格式无效，请使用600或六百等明确整数。")
    return high_value * 10_000 + low_value


def _percentage(match: re.Match[str], group: str) -> int:
    if bool(match.group(group + "_prefix")) == bool(match.group(group + "_symbol")):
        raise ValueError("字数百分比格式无效，请使用80%或百分之八十。")
    return _number(match.group(group))


def _validate_base(source_text: str | None, source_characters: int | None,
                   original_characters: int | None) -> int | None:
    if source_text is not None and not isinstance(source_text, str):
        raise ValueError("source_text must be a string")
    provided = [value for value in (source_characters, original_characters)
                if value is not None]
    if len(provided) == 2 and provided[0] != provided[1]:
        raise ValueError("source_characters and original_characters disagree")
    base = provided[0] if provided else None
    if base is not None:
        if isinstance(base, bool) or not isinstance(base, int) or base < 0:
            raise ValueError("source character count must be a non-negative integer")
    if source_text is not None:
        counted = count_characters(source_text)
        if base is not None and base != counted:
            raise ValueError("source character count does not match source_text")
        base = counted
    return base


def _scope(source_scope: str | None) -> str:
    if source_scope is None:
        return "request_scope"
    if not isinstance(source_scope, str) or not source_scope.strip():
        raise ValueError("source_scope must be a non-empty string")
    aliases = {
        "selected": "selection",
        "selected_text": "selection",
        "selected_range": "selection",
        "full": "chapter",
        "full_text": "chapter",
        "document": "chapter",
    }
    value = source_scope.strip().lower()
    return aliases.get(value, value)


def _valid_percent_pair(minimum: int | None, maximum: int | None) -> None:
    for value in (minimum, maximum):
        if value is not None and (value < 1 or value > 1000):
            raise ValueError("relative percentage must be between 1 and 1000")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("relative percentage lower bound cannot exceed upper bound")


def _valid_absolute_pair(minimum: int | None, maximum: int | None) -> None:
    for value in (minimum, maximum):
        if value is not None and value < 1:
            raise ValueError("character limits must be positive")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("character lower bound cannot exceed upper bound")


def _nearby_clause(instruction: str, start: int) -> str:
    """Return the short instruction clause immediately before a number."""
    boundary = max(
        instruction.rfind("\n", 0, start),
        instruction.rfind("。", 0, start),
        instruction.rfind("！", 0, start),
        instruction.rfind("？", 0, start),
        instruction.rfind(";", 0, start),
        instruction.rfind("；", 0, start),
    )
    return instruction[max(boundary + 1, start - 120):start]


def _is_explicit_length_clause(instruction: str, start: int, task: str) -> bool:
    """Require a nearby writing/length marker before accepting absolute digits."""
    clause = _nearby_clause(instruction, start)
    # Imperative output lengths must take precedence over the story "有" guard.
    if re.search(
        r"(?:^|[，,:：]|\s)(?:正文|候选(?:正文)?|输出(?:正文)?)?"
        r"(?:字数|长度|篇幅)?\s*(?:要有|需要有|必须有|应有|应当有|须有)"
        r"\s*(?:大约|约)?\s*$", clause
    ):
        return True
    if re.search(r"(?:保留|写有|写着|刻有|印有|印着|共有|只有|包含|有)\s*(?:大约|约)?\s*$", clause):
        return False
    return bool(_TASK_WORDS.search(clause) or _CONTROL_WORDS.search(clause)
                or task in _BODY_TASKS and re.search(r"(?:正文|候选|输出).{0,12}"
                                                     r"(?:需要|控制|限制|保持)", clause))


def _is_explicit_relative_clause(instruction: str, start: int, task: str) -> bool:
    """Require a length marker before accepting a percentage phrase."""
    clause = _nearby_clause(instruction, start)
    return bool(
        re.search(r"(?:字数|长度|篇幅|字符|正文|候选|输出|控制|限制|"
                  r"字数范围|长度范围)", clause)
        or re.search(r"保持(?:在|为)?\s*(?:原文|全文|原稿)", clause)
    )


def _base_range(minimum: int | None, maximum: int | None, base: int | None,
                relative_minimum: int | None, relative_maximum: int | None
                ) -> tuple[int | None, int | None, str]:
    if base is None or base == 0:
        return None, None, "explicit_relative_unresolved"
    minimum_chars = math.ceil(base * relative_minimum / 100) if relative_minimum is not None else None
    maximum_chars = math.floor(base * relative_maximum / 100) if relative_maximum is not None else None
    _valid_absolute_pair(minimum_chars, maximum_chars)
    return minimum_chars, maximum_chars, "explicit_relative"


def _range_target(instruction: str, minimum: int, maximum: int) -> int:
    targets = {_number(match.group("value")) for match in _SOFT_TARGET_RE.finditer(instruction)}
    if any(not minimum <= target <= maximum for target in targets):
        raise ValueError("正文长度目标必须在已指定的字数范围内")
    if len(targets) > 1:
        raise ValueError("正文长度目标不一致，请保留一个明确目标")
    return next(iter(targets)) if targets else (minimum + maximum) // 2


def _mask_quoted_requirements(instruction: str) -> str:
    def masked(match: re.Match[str]) -> str:
        inner = match.group()[1:-1]
        value = inner.strip().rstrip("。.").rstrip()
        direct = re.search(
            r"(?:^|[,，])\s*(?:正文(?:字数|长度)?|候选(?:正文)?|输出(?:正文)?|字数|篇幅|长度)"
            r"\s*(?:控制(?:在|为)?|限制(?:在|为)?|保持(?:在|为)?|要求(?:为|是)?|"
            r"目标(?:为|是)?|范围(?:为|是)?|必须(?:有)?|要有|需要|至少|最多|不超过|不少于)?"
            r"\s*[:：]?\s*$",
            _nearby_clause(instruction, match.start()).lstrip(" \t”’」』\"'"))
        pure_length = any(pattern.fullmatch(value) for pattern in (
            _ABSOLUTE_RANGE_RE, _ABSOLUTE_EXACT_RE, _ABSOLUTE_MIN_RE, _ABSOLUTE_MAX_RE,
            _RELATIVE_RANGE_RE, _RELATIVE_MIN_RE, _RELATIVE_MAX_RE))
        # Quoted bounds remain instructions only when explicitly introduced as
        # the output length; character dialogue and manuscript excerpts do not.
        return " " + inner + " " if direct and pure_length else " " * len(match.group())

    return _QUOTED_REQUIREMENT_RE.sub(masked, instruction)


def output_contract(
    task: str,
    instruction: str,
    *,
    source_text: str | None = None,
    source_characters: int | None = None,
    original_characters: int | None = None,
    source_scope: str | None = None,
) -> dict[str, Any]:
    """Parse an explicit prose-length requirement without inventing one.

    ``source_text`` is the exact body or selection being transformed.  For a
    relative requirement, omitting it leaves the contract unresolved instead
    of silently using a whole-chapter body from some outer context.
    """
    if not isinstance(task, str) or not task:
        raise ValueError("task must be a non-empty string")
    if not isinstance(instruction, str):
        raise ValueError("instruction must be a string")
    scope = _scope(source_scope)
    base = _validate_base(source_text, source_characters, original_characters)
    contract: dict[str, Any] = {
        "minimum_characters": None,
        "maximum_characters": None,
        "target_characters": None,
        "counting": COUNTING_RULE,
        "source": "unspecified",
        "source_scope": scope,
        "source_characters": base,
        "relative_minimum_percent": None,
        "relative_maximum_percent": None,
    }
    if task not in _BODY_TASKS or not instruction.strip():
        return contract
    instruction = _mask_quoted_requirements(instruction)

    relative_match = next(
        (match for match in _RELATIVE_RANGE_RE.finditer(instruction)
         if _is_explicit_relative_clause(instruction, match.start(), task)), None)
    relative_minimum: int | None = None
    relative_maximum: int | None = None
    relative_start: int | None = None
    if relative_match:
        relative_minimum = _percentage(relative_match, "minimum")
        relative_maximum = _percentage(relative_match, "maximum")
        relative_start = relative_match.start()
    else:
        relative_match_one_sided = next(
            (match for match in _RELATIVE_MIN_RE.finditer(instruction)
             if _is_explicit_relative_clause(instruction, match.start(), task)), None)
        if relative_match_one_sided:
            relative_minimum = _percentage(relative_match_one_sided, "value")
            relative_start = relative_match_one_sided.start()
        else:
            relative_match_one_sided = next(
                (match for match in _RELATIVE_MAX_RE.finditer(instruction)
                 if _is_explicit_relative_clause(instruction, match.start(), task)), None)
            if relative_match_one_sided:
                relative_maximum = _percentage(relative_match_one_sided, "value")
                relative_start = relative_match_one_sided.start()
    if relative_start is not None:
        _valid_percent_pair(relative_minimum, relative_maximum)
        minimum, maximum, source = _base_range(
            None, None, base, relative_minimum, relative_maximum)
        contract.update({
            "minimum_characters": minimum,
            "maximum_characters": maximum,
            "source": source,
            "relative_minimum_percent": relative_minimum,
            "relative_maximum_percent": relative_maximum,
        })
        if minimum is not None and maximum is not None:
            contract["target_characters"] = _range_target(instruction, minimum, maximum)
        elif minimum is not None:
            contract["target_characters"] = minimum
        elif maximum is not None:
            contract["target_characters"] = maximum
        return contract

    # Prefer a range over an exact match.  The ranges are searched first so
    # the lower/upper numbers are not mistaken for a standalone exact target.
    absolute_match = next(
        (match for match in _ABSOLUTE_RANGE_RE.finditer(instruction)
         if _is_explicit_length_clause(instruction, match.start(), task)), None)
    if absolute_match:
        minimum = _number(absolute_match.group("minimum"))
        maximum = _number(absolute_match.group("maximum"))
        _valid_absolute_pair(minimum, maximum)
        contract.update({
            "minimum_characters": minimum,
            "maximum_characters": maximum,
            "target_characters": _range_target(instruction, minimum, maximum),
            "source": "explicit_absolute",
        })
        return contract

    minimum_match = next(
        (match for match in _ABSOLUTE_MIN_RE.finditer(instruction)
         if _is_explicit_length_clause(instruction, match.start(), task)), None)
    maximum_match = next(
        (match for match in _ABSOLUTE_MAX_RE.finditer(instruction)
         if _is_explicit_length_clause(instruction, match.start(), task)), None)
    if minimum_match or maximum_match:
        minimum = _number(minimum_match.group("value")) if minimum_match else None
        maximum = _number(maximum_match.group("value")) if maximum_match else None
        _valid_absolute_pair(minimum, maximum)
        contract.update({
            "minimum_characters": minimum,
            "maximum_characters": maximum,
            "target_characters": minimum if minimum is not None else maximum,
            "source": "explicit_absolute",
        })
        return contract

    # An exact number is accepted only when it is directly tied to a writing
    # action or a length-control word.  This intentionally does not infer a
    # range from arbitrary numbers in the author's scene instructions.
    soft_target_starts = {
        match.start("value") for match in _SOFT_TARGET_RE.finditer(instruction)
    }
    for match in _ABSOLUTE_EXACT_RE.finditer(instruction):
        if match.start("value") in soft_target_starts:
            continue
        if not _is_explicit_length_clause(instruction, match.start(), task):
            continue
        # If a range was present but malformed, do not reinterpret its first
        # number as an exact target.
        prefix = instruction[max(0, match.start() - 3):match.start()]
        if prefix.rstrip().endswith(("至", "到", "-", "~", "～")):
            continue
        value = _number(match.group("value"))
        _valid_absolute_pair(value, value)
        contract.update({
            "minimum_characters": value,
            "maximum_characters": value,
            "target_characters": value,
            "source": "explicit_absolute",
        })
        return contract
    return contract


def _contract_from(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return output_contract("continue", "")
    if not isinstance(value, Mapping):
        raise ValueError("contract must be an object")
    contract = {
        "minimum_characters": value.get("minimum_characters"),
        "maximum_characters": value.get("maximum_characters"),
        "target_characters": value.get("target_characters"),
        "counting": value.get("counting", COUNTING_RULE),
        "source": value.get("source", "unspecified"),
        "source_scope": value.get("source_scope", "request_scope"),
        "source_characters": value.get("source_characters"),
        "relative_minimum_percent": value.get("relative_minimum_percent"),
        "relative_maximum_percent": value.get("relative_maximum_percent"),
    }
    minimum, maximum = contract["minimum_characters"], contract["maximum_characters"]
    if (minimum is not None and (isinstance(minimum, bool) or not isinstance(minimum, int))) or \
            (maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int))):
        raise ValueError("contract character limits must be integers")
    _valid_absolute_pair(minimum, maximum)
    source_characters = contract["source_characters"]
    if source_characters is not None and (
            isinstance(source_characters, bool)
            or not isinstance(source_characters, int)
            or source_characters < 0):
        raise ValueError("contract source character count must be a non-negative integer")
    target = contract["target_characters"]
    if target is not None and (
            isinstance(target, bool) or not isinstance(target, int) or target < 1):
        raise ValueError("contract target character count must be a positive integer")
    if target is not None and minimum is not None and target < minimum:
        raise ValueError("contract target is below its lower bound")
    if target is not None and maximum is not None and target > maximum:
        raise ValueError("contract target is above its upper bound")
    return contract


def find_planning_contamination(task: str, text: str) -> dict[str, Any] | None:
    """Return one high-confidence planning marker, without changing ``text``."""
    if not isinstance(text, str) or not text:
        return None
    tagged = _OPEN_TAG_RE.search(text)
    if tagged:
        return {
            "code": "planning_contamination",
            "description": "候选包含明显的模型规划或思考标记，需独立核查。",
            "quote": tagged.group(0).strip()[:160],
        }
    if task not in _BODY_TASKS:
        return None
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped:
            continue
        # A quoted line is ordinary dialogue even when the quoted words happen
        # to begin with "我们需要..." or "Analysis:".
        if stripped[0] in _DIALOGUE_PREFIX:
            continue
        for pattern in (_LINE_META_RE, _LINE_WRITING_META_RE,
                        _LINE_PLANNING_RE, _LINE_ENGLISH_META_RE):
            match = pattern.search(line)
            if match:
                return {
                    "code": "planning_contamination",
                    "description": "候选包含明显的模型规划或思考标记，需独立核查。",
                    "quote": match.group(0).strip()[:160],
                }
    return None


def output_prompt_constraints(task: str, contract: Mapping[str, Any] | None) -> str:
    """Build extra mechanical output guidance for a provider prompt.

    This is intentionally separate from the author's instruction.  Callers can
    append the returned text to a system or task contract without mutating the
    author's words.
    """
    if task not in _BODY_TASKS:
        return ""
    checked = _contract_from(contract)
    lines: list[str] = []
    minimum, maximum = checked["minimum_characters"], checked["maximum_characters"]
    if minimum is not None and maximum is not None:
        target = checked["target_characters"]
        if target is None:
            target = (minimum + maximum) // 2
        lines.append(f"正文长度目标约{target}字，允许{minimum}至{maximum}字。")
    elif minimum is not None:
        lines.append(f"正文至少{minimum}字。")
    elif maximum is not None:
        lines.append(f"正文最多{maximum}字。")
    elif checked["source"] == "explicit_relative_unresolved":
        low = checked.get("relative_minimum_percent")
        high = checked.get("relative_maximum_percent")
        if low is not None and high is not None:
            lines.append(f"正文长度保持原文的{low}%至{high}%。")
        elif low is not None:
            lines.append(f"正文长度至少为原文的{low}%。")
        elif high is not None:
            lines.append(f"正文长度最多为原文的{high}%。")
    if minimum is not None or maximum is not None or checked["source"] == "explicit_relative_unresolved":
        lines.append("字数按去除空白后的字符数计算，标点、数字和字母均计入。")
    if maximum is not None:
        if task == "continue":
            if minimum is not None and maximum <= 1000:
                lines.append("按场景需要分配各段篇幅，为结尾留出字数；围绕作者要求推进关键行动或选择，"
                             "用其直接结果收束当前片段，保留作者指定情节与因果，不输出段落预算或检查过程。")
            else:
                lines.append("按场景需要分配各段篇幅，为结尾留出字数；在上限前收束完整场景，"
                             "保留关键动作与因果，不输出段落预算或检查过程。")
        else:
            lines.append("按原文内容分配各段篇幅，在上限内完整保留事件、事实与收束，"
                         "不输出段落预算或检查过程。")
    lines.append("只返回正文，不要解释、分析、思考过程、标题或代码围栏。")
    return "\n".join(lines)


# Short alias for callers that prefer the noun used in prompt assembly.
prompt_constraints = output_prompt_constraints
build_output_contract = output_contract
prompt_output_constraints = output_prompt_constraints


def validate_output(
    task: str,
    text: str,
    contract: Mapping[str, Any] | None = None,
    *,
    instruction: str | None = None,
    source_text: str | None = None,
    source_characters: int | None = None,
    original_characters: int | None = None,
    source_scope: str | None = None,
    protocol_complete: bool | None = None,
) -> dict[str, Any]:
    """Perform mechanical candidate checks and return a stable result object."""
    if contract is None:
        contract = output_contract(
            task,
            instruction or "",
            source_text=source_text,
            source_characters=source_characters,
            original_characters=original_characters,
            source_scope=source_scope,
        )
    checked = _contract_from(contract)
    issues: list[dict[str, Any]] = []
    if protocol_complete is False:
        issues.append({
            "code": "protocol_incomplete",
            "description": "模型回复未具备完整正文协议证据。",
        })
    if not isinstance(text, str):
        text = ""
    if not text.strip():
        issues.append({"code": "empty_output", "description": "模型没有返回有效文本。"})
    characters = count_characters(text)
    minimum, maximum = checked["minimum_characters"], checked["maximum_characters"]
    if checked["source"] == "explicit_relative_unresolved":
        issues.append({
            "code": "length_base_missing",
            "description": "相对字数要求缺少本次请求原文范围，无法安全核对长度。",
        })
    if minimum is not None and characters < minimum or maximum is not None and characters > maximum:
        if minimum is not None and maximum is not None:
            description = f"候选为{characters}字，要求{minimum}至{maximum}字。"
        elif minimum is not None:
            description = f"候选为{characters}字，要求不少于{minimum}字。"
        else:
            description = f"候选为{characters}字，要求不超过{maximum}字。"
        issues.append({"code": "length_out_of_range", "description": description})
    contamination = find_planning_contamination(task, text)
    if contamination:
        issues.append(contamination)
    return {
        "protocol_complete": protocol_complete,
        "characters": characters,
        "contract": checked,
        "output_contract": checked,
        "issues": issues,
        "eligible": not issues,
        "eligible_for_blind_review": not issues,
        "hard_fact_status": "requires_independent_review",
        "scope": "字数和明显规划标记为机械检查；通过不证明事实、文风或文学质量。",
    }


# Product-facing spelling and the literary helper spelling are both useful.
validate_body_output = validate_output
validate_prose_output = validate_output


__all__ = [
    "COUNTING_RULE",
    "count_characters",
    "count_non_whitespace_characters",
    "build_output_contract",
    "find_planning_contamination",
    "output_contract",
    "output_prompt_constraints",
    "prompt_output_constraints",
    "prompt_constraints",
    "validate_body_output",
    "validate_output",
    "validate_prose_output",
]
