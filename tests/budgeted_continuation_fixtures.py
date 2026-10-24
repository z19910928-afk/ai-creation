#!/usr/bin/env python3
"""多轮字数预算续写引擎的可执行回归 fixture。

运行：``.venv/bin/python tests/budgeted_continuation_fixtures.py``

覆盖：
- 5 个停止原因：TARGET_REACHED / NO_PROGRESS / MAX_ROUNDS / BUDGET_EXHAUSTED / CANCELED
- 目标容差 ±10% 的上下边界（0.9 倍下限、1.1 倍上限）
- 引擎不修改调用方正文：运行前后正文内容与 sha256 完全一致
- 参数越界、回调非法、generate 返回值非法时抛 ValueError
- 返回值可 JSON 序列化，且各轮记账自洽
- v1.1：停滞重试（retried/重试上限）、弹性轮数（extended_rounds/60% 门槛）、
  余量提示（closing_hint 只在剩余 < 600 时出现，自定义 builder 不强制）
- v1.2：超冲保护（每轮上下文带 max_characters/overshoot_hint，首轮由
  round_budget() 交给调用方）、停滞换策略（三次展开策略轮换 + 本次至少 N 字，
  默认重试 1 次）；生成侧无视提示时如实记录 within_tolerance=False 与
  overshoot_guard.characters_over_upper，正文一字不裁
- v1.0/v1.1 语义用例通过显式参数锁定：v1.0 用 max_stall_retries=0 /
  hard_max_rounds=max_rounds / closing_hint=False / overshoot_guard=False /
  stall_strategy=False；v1.1 用 stall_strategy=False 复现旧停滞提示文本

本 fixture 只用内存 fake，不访问模型、数据库或网络。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.moyu_continuation import (  # noqa: E402
    BUDGET_EXHAUSTED,
    CANCELED,
    CLOSING_HINT_THRESHOLD,
    DEFAULT_HARD_MAX_ROUNDS,
    DEFAULT_MAX_STALL_RETRIES,
    MAX_ROUNDS,
    NO_PROGRESS,
    STALL_STRATEGIES,
    STOP_REASONS,
    TARGET_REACHED,
    round_budget,
    run_budgeted_continuation,
    target_range,
)


def prose(length: int) -> str:
    """生成恰好 ``length`` 个非空白字符的测试文本。"""
    return "字" * length


class FakeGenerator:
    """按轮返回固定长度文本的内存 fake，并记录每次调用参数。"""

    def __init__(self, lengths: list[int]) -> None:
        if not lengths:
            raise ValueError("FakeGenerator 至少需要一轮长度。")
        self.lengths = list(lengths)
        self.calls: list[dict] = []

    def __call__(self, prompt_context, round_index: int) -> dict:
        self.calls.append({"round_index": round_index, "prompt_context": prompt_context})
        index = round_index - 1
        length = self.lengths[index] if index < len(self.lengths) else self.lengths[-1]
        # 额外字段故意放入不可 JSON 序列化的对象：引擎必须忽略它们，
        # 否则 result 的 json.dumps 会失败，可锁死“返回值可序列化”的约定。
        return {"text": prose(length), "round_index": round_index, "raw": object()}


def test_target_reached_and_accounting() -> None:
    """三轮回合达到 3000 字目标：停止、记账与 JSON 序列化都要自洽。"""
    fake = FakeGenerator([1000, 1000, 1000])
    budget_checks: list[int] = []

    def budget_check(round_index: int) -> bool:
        budget_checks.append(round_index)
        return True

    result = run_budgeted_continuation(
        fake, target_characters=3000, budget_check=budget_check
    )

    assert result["stop_reason"] == TARGET_REACHED, result["stop_reason"]
    assert result["round_count"] == 3
    assert result["characters"] == 3000
    assert result["target_reached"] is True
    assert result["within_tolerance"] is True
    assert result["target_range"] == {"minimum": 2700, "maximum": 3300}
    assert result["candidate_only"] is True
    assert result["counting"] == "non_whitespace_characters_including_punctuation"

    assert [item["round_index"] for item in result["rounds"]] == [1, 2, 3]
    assert [item["cumulative_characters"] for item in result["rounds"]] == [1000, 2000, 3000]
    assert result["text"] == "\n\n".join(item["text"] for item in result["rounds"])
    assert result["characters"] == sum(item["characters"] for item in result["rounds"])
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result

    # 首轮上下文为空字典，后续轮次带上一轮累积信息 + v1.2 超冲额度。
    assert fake.calls[0]["prompt_context"] == {}
    assert fake.calls[1]["prompt_context"] == {
        "previous_text": prose(1000),
        "round_index": 2,
        "characters": 1000,
        "remaining_characters": 2000,
        "max_characters": 2300,
        "overshoot_hint": (
            "本轮最多再写 2300 字，累计不要超过 3300 字（目标 +10% 上限），超出即越界。"
        ),
    }
    # 达标后不再检查预算（第 3 轮开始前检查过，生成后即停止）。
    assert budget_checks == [1, 2, 3]


def test_tolerance_lower_boundary() -> None:
    """累计正好 0.9 倍目标即达标；且目标判定优先于 NO_PROGRESS。"""
    fake = FakeGenerator([899, 1])
    result = run_budgeted_continuation(fake, target_characters=1000, min_round_gain=50)
    assert result["target_range"] == {"minimum": 900, "maximum": 1100}
    assert result["characters"] == 900
    assert result["stop_reason"] == TARGET_REACHED
    # 第二轮只增 1 字（低于 min_round_gain），但已达标，不能误判成 NO_PROGRESS。
    assert result["round_count"] == 2


def test_tolerance_just_below_lower_boundary() -> None:
    """899 < 900：未达标，用满轮数后报 MAX_ROUNDS。

    ``hard_max_rounds=1`` 关闭 v1.1 弹性轮数，锁定 v1.0 的 MAX_ROUNDS 语义。
    """
    fake = FakeGenerator([899])
    result = run_budgeted_continuation(
        fake, target_characters=1000, max_rounds=1, hard_max_rounds=1
    )
    assert result["characters"] == 899
    assert result["stop_reason"] == MAX_ROUNDS
    assert result["target_reached"] is False
    assert result["within_tolerance"] is False


def test_tolerance_upper_boundary() -> None:
    """正好 1.1 倍目标仍在容差内；超 1 字则达标但超出容差。"""
    at_limit = run_budgeted_continuation(FakeGenerator([1100]), target_characters=1000)
    assert at_limit["stop_reason"] == TARGET_REACHED
    assert at_limit["within_tolerance"] is True

    over_limit = run_budgeted_continuation(FakeGenerator([1101]), target_characters=1000)
    assert over_limit["stop_reason"] == TARGET_REACHED
    assert over_limit["target_reached"] is True
    assert over_limit["within_tolerance"] is False


def test_no_progress() -> None:
    """单轮增量低于 min_round_gain 时立即停止，不再空转。

    ``max_stall_retries=0`` 关闭 v1.1 停滞重试，锁定 v1.0 的立即停止语义。
    """
    fake = FakeGenerator([500, 49])
    result = run_budgeted_continuation(
        fake, target_characters=3000, min_round_gain=50, max_stall_retries=0
    )
    assert result["stop_reason"] == NO_PROGRESS
    assert result["round_count"] == 2
    assert result["characters"] == 549
    assert result["target_reached"] is False


def test_no_progress_on_empty_text() -> None:
    """首轮返回空文本：零增量同样判定为 NO_PROGRESS（关闭重试）。"""
    fake = FakeGenerator([0])
    result = run_budgeted_continuation(fake, target_characters=3000, max_stall_retries=0)
    assert result["stop_reason"] == NO_PROGRESS
    assert result["round_count"] == 1
    assert result["text"] == ""
    assert result["characters"] == 0


def test_max_rounds() -> None:
    """每轮都有增量但始终没达标：用满 max_rounds。"""
    fake = FakeGenerator([100])
    result = run_budgeted_continuation(fake, target_characters=3000, max_rounds=3)
    assert result["stop_reason"] == MAX_ROUNDS
    assert result["round_count"] == 3
    assert result["characters"] == 300


def test_min_round_gain_boundary_is_inclusive() -> None:
    """单轮增量正好等于 min_round_gain 不算 NO_PROGRESS（边界必须是 <）。"""
    fake = FakeGenerator([100])
    result = run_budgeted_continuation(
        fake, target_characters=3000, max_rounds=2, min_round_gain=100
    )
    assert result["stop_reason"] == MAX_ROUNDS
    assert result["round_count"] == 2


def test_context_builder_not_called_after_final_round() -> None:
    """用满最后一轮后不再构造下一轮上下文，避免无用的模型调用准备。"""
    fake = FakeGenerator([100])
    builder_calls: list[int] = []

    def build_context(previous_context, record):
        builder_calls.append(record["round_index"])
        return {}

    result = run_budgeted_continuation(
        fake, target_characters=3000, max_rounds=2, context_builder=build_context
    )
    assert result["stop_reason"] == MAX_ROUNDS
    assert result["round_count"] == 2
    assert builder_calls == [1]


def test_budget_exhausted() -> None:
    """budget_check 在第 3 轮返回假值：第 3 轮不得调用 generate。"""
    fake = FakeGenerator([1000])
    checks: list[int] = []

    def budget_check(round_index: int) -> bool:
        checks.append(round_index)
        return round_index <= 2

    result = run_budgeted_continuation(
        fake, target_characters=3000, max_rounds=6, budget_check=budget_check
    )
    assert result["stop_reason"] == BUDGET_EXHAUSTED
    assert result["round_count"] == 2
    assert result["characters"] == 2000
    assert checks == [1, 2, 3]
    assert [call["round_index"] for call in fake.calls] == [1, 2]


def test_budget_exhausted_before_first_round() -> None:
    """首轮前预算就耗尽：零轮、空候选，且不调用 generate。"""
    fake = FakeGenerator([1000])
    result = run_budgeted_continuation(
        fake, target_characters=3000, budget_check=lambda round_index: False
    )
    assert result["stop_reason"] == BUDGET_EXHAUSTED
    assert result["round_count"] == 0
    assert result["text"] == ""
    assert result["characters"] == 0
    assert fake.calls == []


def test_canceled() -> None:
    """第二轮开始前作者取消：已生成的第一轮保留，第二轮不调用 generate。"""
    fake = FakeGenerator([1000])
    decisions = iter([False, True])

    result = run_budgeted_continuation(
        fake, target_characters=3000, max_rounds=6, should_cancel=lambda: next(decisions)
    )
    assert result["stop_reason"] == CANCELED
    assert result["round_count"] == 1
    assert result["characters"] == 1000
    assert [call["round_index"] for call in fake.calls] == [1]


def test_cancel_takes_priority_over_budget() -> None:
    """同时触发取消与预算耗尽时，取消优先，且不再调用预算检查。"""
    fake = FakeGenerator([1000])
    budget_calls: list[int] = []

    def budget_check(round_index: int) -> bool:
        budget_calls.append(round_index)
        return False

    result = run_budgeted_continuation(
        fake,
        target_characters=3000,
        budget_check=budget_check,
        should_cancel=lambda: True,
    )
    assert result["stop_reason"] == CANCELED
    assert result["round_count"] == 0
    assert budget_calls == []
    assert fake.calls == []


def test_custom_context_builder() -> None:
    """context_builder 收到上一轮上下文与轮次记录，返回值原样进入下一轮。"""
    fake = FakeGenerator([400])
    seen: list[tuple[object, dict]] = []

    def build_context(previous_context, record):
        seen.append((previous_context, dict(record)))
        return {"marker": record["round_index"]}

    result = run_budgeted_continuation(
        fake, target_characters=2000, max_rounds=6, context_builder=build_context
    )
    assert result["stop_reason"] == TARGET_REACHED
    assert result["round_count"] == 5  # 400 × 5 = 2000 ≥ 1800
    assert len(seen) == 4              # 只在还要继续时构造下一轮
    assert seen[0][0] == {}
    assert seen[0][1] == {
        "round_index": 1,
        "text": prose(400),
        "characters": 400,
        "cumulative_characters": 400,
    }
    assert fake.calls[1]["prompt_context"] == {"marker": 1}
    assert fake.calls[4]["prompt_context"] == {"marker": 4}


def test_stall_retry_recovers() -> None:
    """停滞轮触发一次重试：重试轮计入 rounds、标记 retried，并带提示上下文。"""
    fake = FakeGenerator([500, 40, 900, 900, 900])
    result = run_budgeted_continuation(fake, target_characters=3000, min_round_gain=50)
    assert result["stop_reason"] == TARGET_REACHED
    assert result["round_count"] == 5
    assert result["characters"] == 3240
    assert result["retries"] == 1
    assert result["extended_rounds"] == 0
    assert "retried" not in result["rounds"][1]
    assert result["rounds"][2]["retried"] is True

    retry_context = fake.calls[2]["prompt_context"]
    assert retry_context["previous_text"] == prose(540)  # 500 + 40，停滞轮文本已注入
    # 配额按「距下限 2700 的缺口 2160 / 剩余 10 轮」= 216 字算，不再按剩余额度摊平。
    assert retry_context["stall_retry_hint"] == (
        "上一轮只写了 40 字，低于单轮有效增量下限 50 字。请改变展开方式："
        "新增一个具体动作、一段对话或一个场景细节；不要重复上一段的内容，"
        "本次至少写 216 字。"
    )
    assert retry_context["stall_retry_attempt"] == 1
    assert retry_context["stall_retry_min_characters"] == 216
    # 恢复期同样带结构化配额，且与重试轮的额度一致。
    assert retry_context["min_characters"] == 216
    assert retry_context["deficit_characters"] == 2160
    assert retry_context["pace_hint"] == (
        "当前距达标下限还差 2160 字，剩余 10 轮。停滞恢复期每轮都要写够配额："
        "本轮至少写 216 字，写完再收束。"
    )
    assert "closing_hint" not in retry_context  # 剩余 2460 字，远未到收束阈值
    assert result["stall_strategy"] is True
    assert result["stall_hints"] == [
        {
            "round_index": 2,
            "characters": 40,
            "attempt": 1,
            "strategy": STALL_STRATEGIES[0],
            "min_characters": 216,
            "hint": retry_context["stall_retry_hint"],
        }
    ]


def test_stall_retry_exhausted() -> None:
    """重试后仍低于阈值：判 NO_PROGRESS，retries 记录实际重试次数。"""
    fake = FakeGenerator([500, 40, 30])
    result = run_budgeted_continuation(
        fake, target_characters=3000, min_round_gain=50, max_stall_retries=1
    )
    assert result["stop_reason"] == NO_PROGRESS
    assert result["round_count"] == 3
    assert result["characters"] == 570
    assert result["retries"] == 1
    assert result["rounds"][2]["retried"] is True


def test_stall_retry_limit_two() -> None:
    """max_stall_retries=2：允许连续两次重试，第二次成功后可继续。"""
    fake = FakeGenerator([500, 40, 30, 900, 900, 900])
    result = run_budgeted_continuation(
        fake, target_characters=3000, min_round_gain=50, max_stall_retries=2
    )
    assert result["stop_reason"] == TARGET_REACHED
    assert result["round_count"] == 6
    assert result["characters"] == 3270
    assert result["retries"] == 2
    assert result["rounds"][2]["retried"] is True
    assert result["rounds"][3]["retried"] is True


def test_elastic_rounds_extend_to_target() -> None:
    """到 max_rounds 时进度 ≥60% 且最近一轮有增量：继续到目标。"""
    fake = FakeGenerator([1000, 1000, 1000])
    result = run_budgeted_continuation(
        fake, target_characters=3000, max_rounds=2, hard_max_rounds=6
    )
    assert result["stop_reason"] == TARGET_REACHED
    assert result["round_count"] == 3
    assert result["characters"] == 3000
    assert result["extended_rounds"] == 1
    assert result["retries"] == 0


def test_elastic_rounds_stop_at_hard_max() -> None:
    """弹性扩展也不是无限：用满 hard_max_rounds 后仍报 MAX_ROUNDS。"""
    fake = FakeGenerator([950, 950, 100, 100])
    result = run_budgeted_continuation(
        fake, target_characters=3000, max_rounds=2, hard_max_rounds=4
    )
    assert result["stop_reason"] == MAX_ROUNDS
    assert result["round_count"] == 4
    assert result["characters"] == 2100
    assert result["extended_rounds"] == 2
    assert result["target_reached"] is False


def test_elastic_requires_sixty_percent() -> None:
    """到 max_rounds 时进度不足 60%：不扩展，按原逻辑停。"""
    fake = FakeGenerator([800])
    result = run_budgeted_continuation(
        fake, target_characters=3000, max_rounds=2, hard_max_rounds=6
    )
    assert result["stop_reason"] == MAX_ROUNDS
    assert result["round_count"] == 2
    assert result["characters"] == 1600
    assert result["extended_rounds"] == 0


def test_elastic_does_not_apply_without_progress() -> None:
    """到 max_rounds 时最近一轮无有效增量：按原逻辑停 NO_PROGRESS。"""
    fake = FakeGenerator([1800, 10])
    result = run_budgeted_continuation(
        fake,
        target_characters=3000,
        max_rounds=2,
        hard_max_rounds=6,
        max_stall_retries=0,
    )
    assert result["stop_reason"] == NO_PROGRESS
    assert result["round_count"] == 2
    assert result["extended_rounds"] == 0


def test_closing_hint_below_threshold() -> None:
    """剩余 <600 时默认上下文注入 closing_hint，N 为剩余字数。"""
    fake = FakeGenerator([450, 450])
    result = run_budgeted_continuation(fake, target_characters=1000)
    assert result["stop_reason"] == TARGET_REACHED
    context = fake.calls[1]["prompt_context"]
    assert context["remaining_characters"] == 550
    assert context["closing_hint"] == "只需再写约 550 字即可收束，不要展开新情节"
    assert "closing_hint" not in fake.calls[0]["prompt_context"]


def test_closing_hint_absent_at_threshold() -> None:
    """剩余正好 600 字不注入（阈值是严格小于），599 才注入。"""
    at_threshold = FakeGenerator([1400, 1400])
    at_result = run_budgeted_continuation(
        at_threshold, target_characters=2000, max_rounds=2, hard_max_rounds=2
    )
    assert at_result["stop_reason"] == TARGET_REACHED
    assert at_threshold.calls[1]["prompt_context"]["remaining_characters"] == 600
    assert "closing_hint" not in at_threshold.calls[1]["prompt_context"]

    below = FakeGenerator([1401, 1401])
    below_result = run_budgeted_continuation(
        below, target_characters=2000, max_rounds=2, hard_max_rounds=2
    )
    assert below_result["stop_reason"] == TARGET_REACHED
    assert below.calls[1]["prompt_context"]["closing_hint"] == (
        "只需再写约 599 字即可收束，不要展开新情节"
    )


def test_closing_hint_not_forced_with_custom_builder() -> None:
    """自定义 context_builder 时不强制注入 closing_hint，由调用方决定。"""
    fake = FakeGenerator([450, 450])
    seen: list[dict] = []

    def build_context(previous_context, record):
        seen.append(dict(record))
        return {"marker": record["round_index"]}

    result = run_budgeted_continuation(
        fake, target_characters=1000, context_builder=build_context
    )
    assert result["stop_reason"] == TARGET_REACHED
    assert all("closing_hint" not in record for record in seen)
    assert fake.calls[1]["prompt_context"] == {"marker": 1}


def test_custom_builder_receives_retry_hint() -> None:
    """自定义 builder 在停滞重试时能从记录里拿到 retry_hint / 至少字数。"""
    fake = FakeGenerator([500, 40, 900, 900, 900])
    seen: list[dict] = []

    def build_context(previous_context, record):
        seen.append(dict(record))
        return {"marker": record["round_index"]}

    result = run_budgeted_continuation(
        fake, target_characters=3000, context_builder=build_context
    )
    assert result["stop_reason"] == TARGET_REACHED
    assert result["retries"] == 1
    assert "retry_hint" not in seen[0]          # 第 1 轮正常继续
    assert seen[1]["round_index"] == 2
    assert seen[1]["retry_hint"] == (
        "上一轮只写了 40 字，低于单轮有效增量下限 50 字。请改变展开方式："
        "新增一个具体动作、一段对话或一个场景细节；不要重复上一段的内容，"
        "本次至少写 216 字。"
    )
    # 结构化字段一并交给调用方：不必解析中文提示也能拿到额度。
    assert seen[1]["retry_attempt"] == 1
    assert seen[1]["retry_min_characters"] == 216
    assert seen[1]["min_characters"] == 216
    assert seen[1]["deficit_characters"] == 2160
    assert "retry_hint" not in seen[2]          # 重试成功后不再重复重试提示
    # 恢复期配额继续下发，且随缺口收敛而下降：1260 缺口 / 9 轮 = 140。
    assert seen[2]["min_characters"] == 140
    assert seen[2]["deficit_characters"] == 1260


def test_v11_parameters_validate() -> None:
    """v1.1 新参数越界必须抛 ValueError，错误文案指明参数名。"""
    fake = FakeGenerator([1000])
    cases = [
        ({"max_rounds": 6, "hard_max_rounds": 5}, "hard_max_rounds"),
        ({"hard_max_rounds": 0}, "hard_max_rounds"),
        ({"hard_max_rounds": 25}, "hard_max_rounds"),
        ({"hard_max_rounds": 2.0}, "hard_max_rounds"),
        ({"max_stall_retries": -1}, "max_stall_retries"),
        ({"max_stall_retries": 13}, "max_stall_retries"),
        ({"max_stall_retries": 1.5}, "max_stall_retries"),
        ({"max_stall_retries": True}, "max_stall_retries"),
    ]
    for kwargs, name in cases:
        try:
            run_budgeted_continuation(fake, **kwargs)
        except ValueError as error:
            assert name in str(error), (kwargs, str(error))
        else:
            raise AssertionError(f"预期 ValueError：{kwargs}")
    assert fake.calls == []


def test_v11_result_fields_json_serializable() -> None:
    """v1.1 新增字段存在且整份结果仍可 JSON 序列化。"""
    result = run_budgeted_continuation(
        FakeGenerator([500, 40, 900, 900, 900]), target_characters=3000
    )
    assert result["retries"] == 1
    assert result["extended_rounds"] == 0
    assert result["max_stall_retries"] == DEFAULT_MAX_STALL_RETRIES
    assert result["hard_max_rounds"] == DEFAULT_HARD_MAX_ROUNDS
    assert isinstance(result["retries"], int)
    assert isinstance(result["extended_rounds"], int)
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


def test_whitespace_is_not_counted() -> None:
    """字符口径为非空白字符：空格、换行、制表符都不计入。"""
    def generate(prompt_context, round_index):
        return {"text": prose(450) + " \n\t "}

    result = run_budgeted_continuation(generate, target_characters=500, max_rounds=1)
    assert result["characters"] == 450
    assert result["stop_reason"] == TARGET_REACHED  # 下限正好 450


def test_parameter_boundaries_are_accepted() -> None:
    """合法边界值必须可用：目标 500/20000、轮数 1/12。"""
    lower = run_budgeted_continuation(
        FakeGenerator([450]), target_characters=500, max_rounds=1
    )
    assert lower["stop_reason"] == TARGET_REACHED
    assert lower["target_range"] == {"minimum": 450, "maximum": 550}

    upper = run_budgeted_continuation(
        FakeGenerator([100]), target_characters=20000, max_rounds=12
    )
    assert upper["stop_reason"] == MAX_ROUNDS
    assert upper["round_count"] == 12
    assert upper["characters"] == 1200


def test_invalid_arguments_raise_value_error() -> None:
    """参数越界或类型错误必须抛 ValueError，且错误文案指明参数名。"""
    fake = FakeGenerator([1000])
    cases = [
        ({"target_characters": 499}, "target_characters"),
        ({"target_characters": 20001}, "target_characters"),
        ({"target_characters": 500.0}, "target_characters"),
        ({"target_characters": True}, "target_characters"),
        ({"target_characters": "3000"}, "target_characters"),
        ({"max_rounds": 0}, "max_rounds"),
        ({"max_rounds": 13}, "max_rounds"),
        ({"max_rounds": 2.0}, "max_rounds"),
        ({"max_rounds": False}, "max_rounds"),
        ({"min_round_gain": -1}, "min_round_gain"),
        ({"min_round_gain": 1.5}, "min_round_gain"),
        ({"min_round_gain": "50"}, "min_round_gain"),
    ]
    for kwargs, name in cases:
        try:
            run_budgeted_continuation(fake, **kwargs)
        except ValueError as error:
            assert name in str(error), (kwargs, str(error))
        else:
            raise AssertionError(f"预期 ValueError：{kwargs}")
    assert fake.calls == [], "参数校验失败时不能调用 generate"

    # 回调本身非法也要拒绝。
    for kwargs, name in [
        ({"generate": None}, "generate"),
        ({"generate": "not-callable"}, "generate"),
        ({"budget_check": 1}, "budget_check"),
        ({"should_cancel": "no"}, "should_cancel"),
        ({"context_builder": 0}, "context_builder"),
    ]:
        try:
            run_budgeted_continuation(**({"generate": fake, **kwargs}))
        except ValueError as error:
            assert name in str(error), (kwargs, str(error))
        else:
            raise AssertionError(f"预期 ValueError：{kwargs}")


def test_invalid_generate_results_raise_value_error() -> None:
    """generate 返回值不符合约定时必须报错，不能静默吞掉。"""
    cases = [
        (lambda context, round_index: None, "generate"),
        (lambda context, round_index: "不是字典", "generate"),
        (lambda context, round_index: {}, "text"),
        (lambda context, round_index: {"text": 123}, "text"),
        (lambda context, round_index: {"text": None}, "text"),
    ]
    for generate, expected in cases:
        try:
            run_budgeted_continuation(generate, target_characters=3000)
        except ValueError as error:
            assert expected in str(error), (expected, str(error))
        else:
            raise AssertionError(f"预期 ValueError：{generate}")


def test_external_body_is_never_mutated() -> None:
    """引擎只产出候选：调用方正文内容与 sha256 前后必须完全一致。"""
    body = "林岚把钥匙放回抽屉，烛火在窗纸上晃了一下。" * 30
    document = {"body": body, "revision_id": "rev-1", "status": "draft"}
    snapshot = dict(document)
    digest_before = hashlib.sha256(body.encode("utf-8")).hexdigest()

    def generate(prompt_context, round_index):
        # 模拟生产：generate 只读取正文，不写回任何内容。
        if round_index > 1:
            assert prompt_context["body"] == body
        return {"text": "新写的候选段落。" * 50, "usage": {"tokens": 10}}

    def build_context(previous_context, record):
        previous_text = ""
        if isinstance(previous_context, dict):
            previous_text = previous_context.get("previous_text", "")
        return {"body": body, "previous_text": previous_text + record["text"]}

    result = run_budgeted_continuation(
        generate, target_characters=2000, max_rounds=6, context_builder=build_context
    )

    assert document == snapshot
    assert document["body"] == body
    assert hashlib.sha256(document["body"].encode("utf-8")).hexdigest() == digest_before
    assert body not in result["text"], "候选文本不能混入原正文"
    assert result["candidate_only"] is True
    assert result["stop_reason"] == TARGET_REACHED


def test_stop_reason_constants() -> None:
    """5 个停止原因常量必须齐全且互不重复。"""
    assert len(STOP_REASONS) == 5
    assert len(set(STOP_REASONS)) == 5
    assert set(STOP_REASONS) == {
        TARGET_REACHED, NO_PROGRESS, MAX_ROUNDS, BUDGET_EXHAUSTED, CANCELED
    }
    assert target_range(3000) == (2700, 3300)
    assert target_range(500) == (450, 550)
    assert target_range(20000) == (18000, 22000)
    assert target_range(1001) == (901, 1101)  # 下限向上取整，不因浮点漂移
    assert CLOSING_HINT_THRESHOLD == 600


def test_overshoot_hint_in_every_round() -> None:
    """v1.2 超冲保护：第 2 轮起每轮都带本轮额度与提示，且额度随累计递减。"""
    fake = FakeGenerator([200, 200, 200, 200, 200])
    result = run_budgeted_continuation(fake, target_characters=1000)

    assert result["stop_reason"] == TARGET_REACHED
    assert fake.calls[0]["prompt_context"] == {}  # 首轮仍由调用方拼装
    second = fake.calls[1]["prompt_context"]
    assert second["max_characters"] == 900          # 1100 - 200
    assert second["overshoot_hint"] == (
        "本轮最多再写 900 字，累计不要超过 1100 字（目标 +10% 上限），超出即越界。"
    )
    assert [call["prompt_context"]["max_characters"] for call in fake.calls[1:]] == [
        900,
        700,
        500,
        300,
    ]
    # 引擎如实记录额度提示送达过哪些轮次。
    assert result["overshoot_guard"] == {
        "enabled": True,
        "upper_characters": 1100,
        "hint_rounds": [2, 3, 4, 5],
        "characters_over_upper": 0,
    }


def test_overshoot_guard_disabled_pins_old_context() -> None:
    """关掉 overshoot_guard 后上下文回到 v1.1 的四键形态（锁定旧行为）。"""
    fake = FakeGenerator([450, 450])
    result = run_budgeted_continuation(
        fake, target_characters=1000, overshoot_guard=False, closing_hint=False
    )
    assert result["stop_reason"] == TARGET_REACHED
    assert fake.calls[1]["prompt_context"] == {
        "previous_text": prose(450),
        "round_index": 2,
        "characters": 450,
        "remaining_characters": 550,
    }
    assert result["overshoot_guard"] == {
        "enabled": False,
        "upper_characters": 1100,
        "hint_rounds": [],
        "characters_over_upper": 0,
    }


def test_v11_stall_hint_text_pinned() -> None:
    """stall_strategy=False 必须逐字节复现 v1.1 的停滞提示文本。"""
    fake = FakeGenerator([500, 40, 900, 900, 900])
    result = run_budgeted_continuation(
        fake,
        target_characters=3000,
        max_stall_retries=1,
        closing_hint=False,
        overshoot_guard=False,
        stall_strategy=False,
    )
    assert result["stop_reason"] == TARGET_REACHED
    assert result["stall_strategy"] is False
    retry_context = fake.calls[2]["prompt_context"]
    assert retry_context["stall_retry_hint"] == (
        "上一轮只写了 40 字，请继续推进情节，本轮写满约 2460 字。"
    )
    assert "stall_retry_attempt" not in retry_context
    assert "stall_retry_min_characters" not in retry_context
    assert result["stall_hints"][0]["strategy"] is None
    assert result["stall_hints"][0]["min_characters"] is None


def test_stall_strategy_changes_per_attempt() -> None:
    """连续两次停滞必须换策略、换提示文本，并把最少字数逐次放大。"""
    fake = FakeGenerator([500, 40, 30, 900, 900, 900])
    result = run_budgeted_continuation(
        fake, target_characters=3000, max_stall_retries=2
    )

    assert result["stop_reason"] == TARGET_REACHED
    assert result["round_count"] == 6
    assert result["characters"] == 3270
    assert result["retries"] == 2
    hints = result["stall_hints"]
    assert [item["round_index"] for item in hints] == [2, 3]
    assert [item["attempt"] for item in hints] == [1, 2]
    assert [item["strategy"] for item in hints] == [
        STALL_STRATEGIES[0],
        STALL_STRATEGIES[1],
    ]
    # 配额按「距下限缺口 / 剩余轮数」算，第 2 次重试再翻倍：
    # 2160/10=216；2130/9=237，×2 = 474。
    assert [item["min_characters"] for item in hints] == [216, 474]
    assert hints[0]["hint"] != hints[1]["hint"]
    assert "新增一个具体动作" in hints[0]["hint"]
    assert "换一个展开角度" in hints[1]["hint"]
    # 第二轮重试用的是第二套策略，不是把第一句提示原样再发一次。
    assert fake.calls[3]["prompt_context"]["stall_retry_attempt"] == 2
    assert fake.calls[3]["prompt_context"]["stall_retry_min_characters"] == 474


def test_overshoot_is_reported_not_trimmed() -> None:
    """生成侧无视额度（sc-15 形态）：如实报超冲，正文一个字都不裁。"""
    fake = FakeGenerator([4661])
    result = run_budgeted_continuation(fake, target_characters=4000)

    assert result["stop_reason"] == TARGET_REACHED
    assert result["characters"] == 4661
    assert result["target_reached"] is True
    assert result["within_tolerance"] is False
    assert result["text"] == prose(4661)          # 原样候选，绝不裁剪
    assert result["overshoot_guard"] == {
        "enabled": True,
        "upper_characters": 4400,
        "hint_rounds": [],                        # 超冲发生在第 1 轮，额度来不及生效
        "characters_over_upper": 261,
    }


def test_round_budget_helper() -> None:
    """round_budget() 供调用方把额度合入首轮开场提示。"""
    start = round_budget(0, 4000)
    assert start["max_characters"] == 4400
    assert start["remaining_characters"] == 4000
    assert start["target_range"] == {"minimum": 3600, "maximum": 4400}
    assert "4400" in start["overshoot_hint"]

    midway = round_budget(3000, 4000)
    assert midway["max_characters"] == 1400
    assert midway["remaining_characters"] == 1000

    exhausted = round_budget(4400, 4000)
    assert exhausted["max_characters"] == 0
    assert exhausted["remaining_characters"] == 0

    for value in (-1, 1.5, True, "0", None):
        try:
            round_budget(value, 4000)
        except ValueError as error:
            assert "characters_done" in str(error), (value, str(error))
        else:
            raise AssertionError(f"预期 ValueError：{value!r}")

    for target in (499, 20001, 4000.0, True):
        try:
            round_budget(0, target)
        except ValueError as error:
            assert "target_characters" in str(error), (target, str(error))
        else:
            raise AssertionError(f"预期 ValueError：{target!r}")


def test_round_target_ratio_default_is_unchanged() -> None:
    """默认（None）逐字节保持 v1.2 行为：上下文键、额度与来源都不变。"""
    fake = FakeGenerator([1000, 1000, 1000])
    result = run_budgeted_continuation(fake, target_characters=3000)
    # 第 2 轮上下文仍是 v1.2 的 6 个键，不含口径审计字段。
    assert fake.calls[1]["prompt_context"] == {
        "previous_text": prose(1000),
        "round_index": 2,
        "characters": 1000,
        "remaining_characters": 2000,
        "max_characters": 2300,
        "overshoot_hint": (
            "本轮最多再写 2300 字，累计不要超过 3300 字（目标 +10% 上限），超出即越界。"
        ),
    }
    assert result["round_target_ratio"] is None
    assert [entry["max_characters"] for entry in result["budget_plan"]] == [
        3300,
        2300,
        1300,
    ]
    assert [entry["source"] for entry in result["budget_plan"]] == [
        "upper",
        "upper",
        "upper",
    ]
    # 首轮上下文按契约是空字典，额度只能由调用方合入开场提示。
    assert result["budget_plan"][0]["delivered"] is False
    assert result["budget_plan"][1]["delivered"] is True
    assert result["characters"] == 3000

    again = run_budgeted_continuation(
        FakeGenerator([1000, 1000, 1000]), target_characters=3000
    )
    assert json.dumps(result, ensure_ascii=False, sort_keys=True) == json.dumps(
        again, ensure_ascii=False, sort_keys=True
    )


def test_round_target_ratio_formula() -> None:
    """给了系数就按目标摊平：T=1200 / d=0 / U=1320 → ceil(1200/1.35)=889。"""
    budget = round_budget(0, 1200, 1.35)
    assert budget["max_characters"] == 889
    assert budget["budget_source"] == "ratio"
    assert budget["formula_characters"] == 889
    assert budget["round_target_ratio"] == 1.35
    assert budget["target_range"] == {"minimum": 1080, "maximum": 1320}
    assert "889" in budget["overshoot_hint"]

    fake = FakeGenerator([500, 500, 500])
    result = run_budgeted_continuation(
        fake, target_characters=1200, round_target_ratio=1.35
    )
    second = fake.calls[1]["prompt_context"]
    assert second["max_characters"] == 519  # ceil((1200 - 500) / 1.35)
    assert second["budget_source"] == "ratio"
    assert second["round_target_ratio"] == 1.35
    assert result["budget_plan"][1]["source"] == "ratio"
    assert result["budget_plan"][1]["planned_max_characters"] == 519


def test_round_target_ratio_clamps_both_ends() -> None:
    """下限 50 与上界 U-d 都生效，source 说明最终由谁决定额度。"""
    floored = round_budget(480, 500, 1.35)  # ceil(20/1.35)=15 → 抬到 50
    assert floored["formula_characters"] == 15
    assert floored["max_characters"] == 50
    assert floored["budget_source"] == "floor"

    capped = round_budget(530, 500, 1.35)  # 上界 550 只剩 20：守上界优先
    assert capped["max_characters"] == 20
    assert capped["budget_source"] == "upper"

    exact = round_budget(0, 500, 1.0)  # ceil(500/1)=500 ≤ 550，未被夹取
    assert exact["max_characters"] == 500
    assert exact["budget_source"] == "ratio"


def test_round_target_ratio_validate() -> None:
    """系数必须是 None 或 1.0–10.0 的数字；布尔值不算数字。"""
    fake = FakeGenerator([1000])
    for value in (0.5, 0, 11, "1.35", True, float("nan"), float("inf")):
        try:
            run_budgeted_continuation(fake, round_target_ratio=value)
        except ValueError as error:
            assert "round_target_ratio" in str(error), (value, str(error))
        else:
            raise AssertionError(f"预期 ValueError：{value!r}")
    assert fake.calls == []
    for value in (1.0, 10.0):
        graded = run_budgeted_continuation(
            FakeGenerator([1000]), round_target_ratio=value
        )
        assert graded["round_target_ratio"] == value


def test_v12_flags_validate() -> None:
    """三个 v1.2 开关必须是布尔值，否则报错且不调用 generate。"""
    fake = FakeGenerator([1000])
    for name in ("closing_hint", "overshoot_guard", "stall_strategy"):
        for value in (1, 0, "yes", None):
            try:
                run_budgeted_continuation(fake, **{name: value})
            except ValueError as error:
                assert name in str(error), (name, value, str(error))
            else:
                raise AssertionError(f"预期 ValueError：{name}={value!r}")
    assert fake.calls == []


def test_default_stall_retries_is_one() -> None:
    """默认只重试 1 次（不额外多花模型调用）：重试轮仍停滞即判 NO_PROGRESS。"""
    assert DEFAULT_MAX_STALL_RETRIES == 1

    stuck = run_budgeted_continuation(
        FakeGenerator([500, 40, 30, 900]), target_characters=3000
    )
    assert stuck["stop_reason"] == NO_PROGRESS
    assert stuck["round_count"] == 3
    assert stuck["retries"] == 1
    assert stuck["characters"] == 570

    # 但这次重试携带的是策略与最少字数，而不是把「请继续写」原样再发一遍。
    assert stuck["stall_hints"] == [
        {
            "round_index": 2,
            "characters": 40,
            "attempt": 1,
            "strategy": STALL_STRATEGIES[0],
            "min_characters": 216,
            "hint": stuck["stall_hints"][0]["hint"],
        }
    ]
    assert "新增一个具体动作" in stuck["stall_hints"][0]["hint"]

    # 调到 2 次后才有第二次机会（每次换下一套策略）。
    recovered = run_budgeted_continuation(
        FakeGenerator([500, 40, 30, 900, 900, 900]),
        target_characters=3000,
        max_stall_retries=2,
    )
    assert recovered["stop_reason"] == TARGET_REACHED
    assert recovered["retries"] == 2


def test_recovery_extends_beyond_max_rounds() -> None:
    """停滞恢复期：配额驱动的续跑可以越过 max_rounds，但受 hard_max_rounds 硬约束。

    这是 sc-18 形态的正解：真停滞 + 服从配额的模型，只要每轮写满
    「距下限缺口 / 剩余轮数」，缺口就会线性收敛到 0。
    """
    class ObedientGenerator:
        """只服从结构化配额（min_characters），其余按最差情况写 30 字。"""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, prompt_context, round_index):
            floor = 0
            if isinstance(prompt_context, dict):
                value = prompt_context.get("min_characters")
                if isinstance(value, int):
                    floor = value
            length = max(30, floor)
            self.calls.append(
                {"round_index": round_index, "prompt_context": prompt_context,
                 "length": length}
            )
            return {"text": prose(length), "round_index": round_index}

    generator = ObedientGenerator()
    result = run_budgeted_continuation(
        generator,
        target_characters=3000,
        max_rounds=3,
        hard_max_rounds=8,
        min_round_gain=50,
    )
    assert result["stop_reason"] == TARGET_REACHED
    assert result["characters"] == 2700          # 恰好追平下限，没有超冲
    assert result["round_count"] == 8
    assert result["extended_rounds"] == 5        # 越过 max_rounds=3 的 5 轮
    assert result["retries"] == 1
    assert result["within_tolerance"] is True
    # 第 1 轮上下文为空，拿不到配额；第 2 轮起每轮都带配额，且逐轮收敛。
    assert generator.calls[0]["prompt_context"] == {}
    assert generator.calls[1]["prompt_context"]["min_characters"] == 382
    assert [call["prompt_context"]["min_characters"] for call in generator.calls[1:]] == [
        382, 382, 382, 381, 381, 381, 381
    ]
    # 每一轮都写了 ≥ min_characters，缺口线性收敛而不是锯齿。
    assert result["characters"] == sum(call["length"] for call in generator.calls)


def test_recovery_requires_the_strategy_flag() -> None:
    """关掉 stall_strategy（v1.0 语义）就没有恢复期续跑：停在 max_rounds。"""
    class ObedientGenerator:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, prompt_context, round_index):
            floor = 0
            if isinstance(prompt_context, dict):
                value = prompt_context.get("min_characters")
                if isinstance(value, int):
                    floor = value
            length = max(30, floor)
            self.calls.append({"prompt_context": prompt_context, "length": length})
            return {"text": prose(length), "round_index": round_index}

    generator = ObedientGenerator()
    result = run_budgeted_continuation(
        generator,
        target_characters=3000,
        max_rounds=3,
        hard_max_rounds=8,
        min_round_gain=50,
        max_stall_retries=0,
        stall_strategy=False,
    )
    # 无重试可用：第 1 轮 30 字就停滞，直接判 NO_PROGRESS（不会续跑）。
    assert result["stop_reason"] == NO_PROGRESS
    assert result["round_count"] == 1
    assert result["characters"] == 30
    assert result["extended_rounds"] == 0
    assert all("min_characters" not in call["prompt_context"] for call in generator.calls)


def test_seed_counts_toward_characters() -> None:
    """seed 计入 characters 与 remaining，且 seed 自身不占轮次。"""
    seed = prose(1000)
    fake = FakeGenerator([1000, 1000])
    result = run_budgeted_continuation(fake, target_characters=3000, seed_text=seed)
    assert result["seed_characters"] == 1000
    assert result["characters"] == 3000
    assert result["round_count"] == 2
    assert result["stop_reason"] == TARGET_REACHED
    first = fake.calls[0]["prompt_context"]
    assert first["characters"] == 1000
    assert first["remaining_characters"] == 2000
    assert first["previous_text"] == seed
    # 第 2 轮已经扣掉 seed 与第 1 轮的字数。
    second = fake.calls[1]["prompt_context"]
    assert second["characters"] == 2000
    assert second["remaining_characters"] == 1000


def test_seed_is_first_round_previous_text() -> None:
    """seed 非空时第 1 轮上下文带 previous_text=seed_text（逐字相等）。"""
    seed = prose(120)
    fake = FakeGenerator([2600])
    result = run_budgeted_continuation(fake, target_characters=3000, seed_text=seed)
    assert fake.calls[0]["round_index"] == 1
    assert fake.calls[0]["prompt_context"]["previous_text"] == seed
    assert fake.calls[0]["prompt_context"]["characters"] == 120
    assert result["stop_reason"] == TARGET_REACHED
    assert result["characters"] == 2720


def test_seed_meeting_lower_bound_skips_provider() -> None:
    """seed 已达下限：0 次 provider 调用、rounds=0、text 就是 seed。"""
    lower, upper = target_range(3000)
    assert (lower, upper) == (2700, 3300)

    seed = prose(lower)
    fake = FakeGenerator([1])
    result = run_budgeted_continuation(fake, target_characters=3000, seed_text=seed)
    assert fake.calls == []
    assert result["round_count"] == 0
    assert result["rounds"] == []
    assert result["stop_reason"] == TARGET_REACHED
    assert result["target_reached"] is True
    assert result["within_tolerance"] is True
    assert result["text"] == seed
    assert result["characters"] == lower
    assert result["seed_characters"] == lower
    assert result["budget_plan"] == []

    # seed 超过上界：停止原因仍是 TARGET_REACHED，但 within_tolerance 如实为假。
    # seed 不许影响容差定义。
    over = prose(upper + 100)
    fake_over = FakeGenerator([1])
    result_over = run_budgeted_continuation(
        fake_over, target_characters=3000, seed_text=over
    )
    assert fake_over.calls == []
    assert result_over["stop_reason"] == TARGET_REACHED
    assert result_over["target_reached"] is True
    assert result_over["within_tolerance"] is False
    assert result_over["text"] == over


def test_seed_plus_one_round_reaches_target() -> None:
    """seed + 一轮补足：正文严格等于 seed 与新一轮的原样拼接。"""
    seed = prose(400)
    generated = prose(2400)
    fake = FakeGenerator([2400])
    result = run_budgeted_continuation(fake, target_characters=3000, seed_text=seed)
    assert result["stop_reason"] == TARGET_REACHED
    assert result["round_count"] == 1
    assert result["text"].startswith(seed)
    assert result["text"] == seed + "\n\n" + generated
    assert result["characters"] == 2800


def test_empty_seed_matches_old_behavior() -> None:
    """空 seed 与旧行为完全一致：三个记账字段与首轮上下文都不变。"""
    def run(seed_text: str | None) -> dict:
        fake = FakeGenerator([1000, 1000, 1000])
        kwargs = {} if seed_text is None else {"seed_text": seed_text}
        result = run_budgeted_continuation(
            fake, target_characters=3000, max_rounds=3, **kwargs
        )
        assert fake.calls[0]["prompt_context"] == {}
        return result

    old = run(None)
    new = run("")
    for field in ("rounds", "stop_reason", "characters", "round_count", "text"):
        assert old[field] == new[field], field
    assert old["seed_characters"] == 0
    assert new["seed_characters"] == 0


def test_whitespace_seed_is_treated_as_empty() -> None:
    """纯空白 seed 的计数为 0，视同没有 seed（不把空白当正文拼接）。"""
    blank = "  \n\t\u3000  "
    fake = FakeGenerator([1000, 1000, 1000])
    result = run_budgeted_continuation(fake, target_characters=3000, seed_text=blank)
    baseline = FakeGenerator([1000, 1000, 1000])
    baseline_result = run_budgeted_continuation(baseline, target_characters=3000)
    assert result["seed_characters"] == 0
    assert result["text"] == baseline_result["text"]
    assert result["characters"] == baseline_result["characters"]
    assert fake.calls[0]["prompt_context"] == {}
    assert "previous_text" not in fake.calls[0]["prompt_context"]


def test_seed_not_counted_in_rounds_and_body_verbatim() -> None:
    """seed 不进 round_metrics；正文按原样拼接，连 seed 的空白都不改。"""
    seed = "  前后空白都要原样  " + prose(30)
    fake = FakeGenerator([2700])
    result = run_budgeted_continuation(fake, target_characters=3000, seed_text=seed)
    assert result["seed_characters"] == 38
    assert len(result["rounds"]) == 1
    only_round = result["rounds"][0]
    assert only_round["characters"] == 2700
    assert only_round["text"] == prose(2700)
    assert only_round["cumulative_characters"] == 2738
    assert result["text"] == seed + "\n\n" + prose(2700)
    assert result["text"][: len(seed)] == seed


def test_seed_with_custom_builder() -> None:
    """自定义 builder 收到「第 0 轮」记录（text=seed、characters=seed 字数）。"""
    seed = prose(60)
    seen: list[dict] = []

    def builder(previous_context, record):
        seen.append({"previous": previous_context, "record": dict(record)})
        return {"previous_text": str(record["text"]), "characters": record["characters"]}

    fake = FakeGenerator([2700])
    result = run_budgeted_continuation(
        fake, target_characters=3000, seed_text=seed, context_builder=builder
    )
    assert len(seen) == 1
    assert seen[0]["record"]["round_index"] == 0
    assert seen[0]["record"]["text"] == seed
    assert seen[0]["record"]["characters"] == 60
    assert fake.calls[0]["prompt_context"] == {"previous_text": seed, "characters": 60}
    assert result["text"] == seed + "\n\n" + prose(2700)


def test_seed_type_validate() -> None:
    """seed_text 不是字符串时抛 ValueError，且不调用 provider。"""
    fake = FakeGenerator([1])
    for bad in (None, 12, b"bytes", ["x"]):
        try:
            run_budgeted_continuation(fake, target_characters=3000, seed_text=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"seed_text={bad!r} 应当抛 ValueError")
    assert fake.calls == []


def main() -> None:
    tests = [
        test_target_reached_and_accounting,
        test_tolerance_lower_boundary,
        test_tolerance_just_below_lower_boundary,
        test_tolerance_upper_boundary,
        test_no_progress,
        test_no_progress_on_empty_text,
        test_max_rounds,
        test_min_round_gain_boundary_is_inclusive,
        test_context_builder_not_called_after_final_round,
        test_budget_exhausted,
        test_budget_exhausted_before_first_round,
        test_canceled,
        test_cancel_takes_priority_over_budget,
        test_custom_context_builder,
        test_stall_retry_recovers,
        test_stall_retry_exhausted,
        test_stall_retry_limit_two,
        test_elastic_rounds_extend_to_target,
        test_elastic_rounds_stop_at_hard_max,
        test_elastic_requires_sixty_percent,
        test_elastic_does_not_apply_without_progress,
        test_closing_hint_below_threshold,
        test_closing_hint_absent_at_threshold,
        test_closing_hint_not_forced_with_custom_builder,
        test_custom_builder_receives_retry_hint,
        test_v11_parameters_validate,
        test_v11_result_fields_json_serializable,
        test_whitespace_is_not_counted,
        test_parameter_boundaries_are_accepted,
        test_invalid_arguments_raise_value_error,
        test_invalid_generate_results_raise_value_error,
        test_external_body_is_never_mutated,
        test_stop_reason_constants,
        test_overshoot_hint_in_every_round,
        test_overshoot_guard_disabled_pins_old_context,
        test_v11_stall_hint_text_pinned,
        test_stall_strategy_changes_per_attempt,
        test_overshoot_is_reported_not_trimmed,
        test_round_budget_helper,
        test_round_target_ratio_default_is_unchanged,
        test_round_target_ratio_formula,
        test_round_target_ratio_clamps_both_ends,
        test_round_target_ratio_validate,
        test_v12_flags_validate,
        test_default_stall_retries_is_one,
        test_recovery_extends_beyond_max_rounds,
        test_recovery_requires_the_strategy_flag,
        test_seed_counts_toward_characters,
        test_seed_is_first_round_previous_text,
        test_seed_meeting_lower_bound_skips_provider,
        test_seed_plus_one_round_reaches_target,
        test_empty_seed_matches_old_behavior,
        test_whitespace_seed_is_treated_as_empty,
        test_seed_not_counted_in_rounds_and_body_verbatim,
        test_seed_with_custom_builder,
        test_seed_type_validate,
    ]
    for test in tests:
        test()
    print("budgeted continuation fixtures: OK", {"cases": len(tests)})


if __name__ == "__main__":
    main()
