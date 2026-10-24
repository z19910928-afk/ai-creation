"""多轮字数预算续写引擎（纯模块，不含 HTTP、数据库与模型调用）。

可选口径（``round_target_ratio``，默认 ``None``，即保持既有行为）：给一个
不小于 1 的系数后，每轮额度从「距上界还剩多少」（``upper - 累计``）改成
「按目标摊平」——``clamp(ceil((target - 累计) / ratio), 50, upper - 累计)``。
真实模型每轮的落点是「额度 × 方差」，把额度钉在 1.1 倍上限会系统性诱导超冲
（实测六轮落点 0.66–1.31 倍额度）；按目标摊平能给方差留出余量。该参数是
opt-in：不传时每轮额度、上下文键与返回结构都与 v1.2 逐字节一致。

v1.2 在 v1.1 之上增加两项「生成前干预」能力，仍然默认开启且不改写正文：

- 超冲保护（``overshoot_guard``）：每轮把「累计字数还剩多少才不越过 1.1 倍
  上限」作为硬约束传进下一轮上下文（``max_characters`` + ``overshoot_hint``），
  让模型在本轮就知道额度；生成侧不服从时如实记录 ``overshoot_guard`` 与
  ``within_tolerance=False``，绝不裁剪正文。
- 停滞换策略（``stall_strategy``）：停滞重试不再复读「请继续写」，而是给出
  「改变展开方式 + 本次至少写 N 字」的具体指令（N 按剩余额度与剩余轮数摊平，
  出现停滞后按「距下限的缺口 / 剩余轮数」逐轮下发配额），
  并允许每次停滞最多重试 ``max_stall_retries`` 次（默认 1），每次换一种策略；
  总轮数仍受 ``hard_max_rounds`` 硬约束。

v1.1 在 v1.0 的循环/记账之上增加三项可控性能力，默认开启且向后兼容：

- 停滞重试：单轮增量低于 ``min_round_gain`` 时，不立即判 ``NO_PROGRESS``，
  而是把停滞轮文本与提示注入下一轮上下文，最多重试 ``max_stall_retries`` 次；
  重试轮计入 ``rounds`` 并标记 ``retried: true``。
- 弹性轮数：到达 ``max_rounds`` 时，若累计进度 ≥ 目标 60% 且最近一轮有有效
  增量，允许继续生成到 ``hard_max_rounds``；停止原因仍为 ``MAX_ROUNDS``。
- 余量提示：剩余字数 < 600 时，默认上下文额外注入 ``closing_hint``，提示收束；
  自定义 ``context_builder`` 时由调用方决定，不强制覆盖。

设计约束：
- 作者控制：本模块只把各轮生成结果拼成候选文本返回，绝不写入正文或 canon；
  是否采纳由作者在前端决定。
- 不裁剪、不改写模型正文：超冲仍如实记录 ``within_tolerance=False``。
- 纯函数：不读写文件、不访问网络、不依赖全局可变状态，便于单测与基准复现。
- 不依赖 moyu_context：下一轮上下文由调用方通过 context_builder 注入，
  避免与上下文装配模块形成循环导入。
- 字符口径：与 moyu_output.count_characters 一致，统计非空白 Unicode 字符
  （标点计入、空白不计），与正文长度校验共用同一把尺子。

典型用法（生产接线由调用方完成）::

    result = run_budgeted_continuation(
        lambda context, round_index: call_model(context, round_index),
        target_characters=3000,
        context_builder=build_next_context,
    )
    if result["stop_reason"] == TARGET_REACHED:
        ...  # 交给候选箱，等待作者采纳，不自动写正文
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from .moyu_output import COUNTING_RULE, count_characters


# 停止原因常量。值是稳定的 snake_case 字符串，可直接落库、写 JSON 或上报基准。
TARGET_REACHED = "target_reached"      # 累计字数达到目标下限（0.9 倍目标）
NO_PROGRESS = "no_progress"            # 重试后单轮增量仍低于 min_round_gain
MAX_ROUNDS = "max_rounds"              # 用满轮数上限仍未达标
BUDGET_EXHAUSTED = "budget_exhausted"  # budget_check 报告预算耗尽
CANCELED = "canceled"                  # should_cancel 报告作者取消

STOP_REASONS = (TARGET_REACHED, NO_PROGRESS, MAX_ROUNDS, BUDGET_EXHAUSTED, CANCELED)

DEFAULT_TARGET_CHARACTERS = 3000
DEFAULT_MAX_ROUNDS = 6
DEFAULT_MIN_ROUND_GAIN = 50
# v1.2：停滞重试默认 1 次（沿用 v1.1 的调用成本），但这次重试会**换策略**；
# 调到 2 次以上时逐次换下一套策略，且每次多一次模型调用（见基准的重试敏感性附录）。
DEFAULT_MAX_STALL_RETRIES = 1
DEFAULT_HARD_MAX_ROUNDS = 12

TARGET_CHARACTERS_LIMITS = (500, 20_000)
MAX_ROUNDS_LIMITS = (1, 12)
MAX_STALL_RETRIES_LIMITS = (0, 12)
HARD_MAX_ROUNDS_LIMITS = (1, 24)
# 对外说明用的容差比例；target_range 内部用整数运算，避免浮点边界漂移。
TARGET_TOLERANCE = 0.1
# 弹性轮数的进度门槛：目标的 60%（用整数运算表达，避免浮点误差）。
EXTENSION_PROGRESS_PERCENT = 60
# 余量提示阈值：剩余字数低于该值时提示收束。
CLOSING_HINT_THRESHOLD = 600
# v1.2 停滞换策略：同一段停滞里每次重试换一种展开方式，避免复读同一句提示。
STALL_STRATEGIES = (
    "新增一个具体动作、一段对话或一个场景细节",
    "换一个展开角度：改写在场人物的反应，或直接推进到下一个事件",
    "把这一段拆成两到三个小事件，每个事件都写清人物、地点和动作",
)
# 可选口径：每轮额度按目标摊平时的系数（默认 None = 用旧口径 upper - 累计）。
DEFAULT_ROUND_TARGET_RATIO = None
ROUND_TARGET_RATIO_LIMITS = (1.0, 10.0)
# 单轮额度下限：按目标摊平后仍不少于这么多字，否则模型会把一轮写成一句话。
MIN_ROUND_BUDGET = 50

__all__ = [
    "BUDGET_EXHAUSTED",
    "CANCELED",
    "CLOSING_HINT_THRESHOLD",
    "DEFAULT_HARD_MAX_ROUNDS",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_MAX_STALL_RETRIES",
    "DEFAULT_MIN_ROUND_GAIN",
    "DEFAULT_ROUND_TARGET_RATIO",
    "DEFAULT_TARGET_CHARACTERS",
    "EXTENSION_PROGRESS_PERCENT",
    "HARD_MAX_ROUNDS_LIMITS",
    "MAX_ROUNDS",
    "MAX_ROUNDS_LIMITS",
    "MAX_STALL_RETRIES_LIMITS",
    "MIN_ROUND_BUDGET",
    "NO_PROGRESS",
    "ROUND_TARGET_RATIO_LIMITS",
    "STALL_STRATEGIES",
    "STOP_REASONS",
    "TARGET_CHARACTERS_LIMITS",
    "TARGET_REACHED",
    "TARGET_TOLERANCE",
    "round_budget",
    "run_budgeted_continuation",
    "target_range",
]


def target_range(target_characters: int) -> tuple[int, int]:
    """返回目标字数 ±10% 对应的整数闭区间 ``(下限, 上限)``。

    下限向上取整、上限向下取整，保证边界判定不因浮点误差漂移。
    例如目标 3000 返回 ``(2700, 3300)``，目标 1000 返回 ``(900, 1100)``。
    """
    lower = (target_characters * 9 + 9) // 10   # ceil(0.9 × 目标)
    upper = (target_characters * 11) // 10      # floor(1.1 × 目标)
    return lower, upper


def _extension_threshold(target_characters: int) -> int:
    """弹性轮数的进度门槛：``ceil(60% × 目标)``，整数运算避免浮点误差。"""
    return (target_characters * EXTENSION_PROGRESS_PERCENT + 99) // 100


def _require_round_target_ratio(value: Any) -> float | None:
    """校验每轮额度系数：None 或 ``ROUND_TARGET_RATIO_LIMITS`` 内的实数。

    布尔值不算数字（``True`` 会被当成 1.0），避免开关被误当系数。
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("round_target_ratio 必须是 None 或大于等于 1 的数字。")
    ratio = float(value)
    minimum, maximum = ROUND_TARGET_RATIO_LIMITS
    if not math.isfinite(ratio) or ratio < minimum or ratio > maximum:
        raise ValueError(
            f"round_target_ratio 必须是 None 或 {minimum:g} 到 {maximum:g} 之间的数字。"
        )
    return ratio


def round_allocation(
    characters_done: int,
    target_characters: int = DEFAULT_TARGET_CHARACTERS,
    round_target_ratio: float | None = None,
) -> dict[str, Any]:
    """算出「本轮最多还能写多少字」以及这个数字是怎么来的（可审计口径）。

    - ``round_target_ratio`` 为 None（默认）：额度 = 上界 - 已写，
      ``source="upper"``，与 v1.2 语义一致。
    - 给了系数：额度 = ``clamp(ceil((目标 - 已写) / 系数), 50, 上界 - 已写)``；
      ``source`` 说明最终由谁决定：``ratio``（按目标摊平）、``floor``（被 50 字
      下限抬起）、``upper``（被上界压下，说明余量已不足。
      ``formula_characters`` 保留未夹取的原始算式结果，便于审计对账。
    """
    if isinstance(characters_done, bool) or not isinstance(characters_done, int):
        raise ValueError("characters_done 必须是大于或等于 0 的整数。")
    if characters_done < 0:
        raise ValueError("characters_done 必须是大于或等于 0 的整数。")
    target_characters = _require_int(
        "target_characters", target_characters, *TARGET_CHARACTERS_LIMITS
    )
    ratio = _require_round_target_ratio(round_target_ratio)
    lower, upper = target_range(target_characters)
    remaining_characters = max(0, target_characters - characters_done)
    ceiling = max(0, upper - characters_done)
    if ratio is None:
        return {
            "characters": characters_done,
            "max_characters": ceiling,
            "source": "upper",
            "formula_characters": None,
            "round_target_ratio": None,
            "target_range": {"minimum": lower, "maximum": upper},
        }
    formula = math.ceil(remaining_characters / ratio) if remaining_characters else 0
    value = max(MIN_ROUND_BUDGET, formula)
    if value > ceiling:
        value = ceiling
        source = "upper"
    elif value > formula:
        source = "floor"
    else:
        source = "ratio"
    return {
        "characters": characters_done,
        "max_characters": value,
        "source": source,
        "formula_characters": formula,
        "round_target_ratio": ratio,
        "target_range": {"minimum": lower, "maximum": upper},
    }


def round_budget(
    characters_done: int,
    target_characters: int = DEFAULT_TARGET_CHARACTERS,
    round_target_ratio: float | None = None,
) -> dict[str, Any]:
    """开场额度：供调用方把超冲保护合入**首轮**提示的公开助手。

    引擎的首轮 ``prompt_context`` 按契约是空字典（首轮提示由调用方拼装），
    所以第 1 轮的额度只能由调用方把本函数返回的字段并进开场提示；
    第 2 轮起引擎自动注入同样的 ``max_characters`` / ``overshoot_hint``。

    ``characters_done`` 是已经计入的字数（例如已写正文长度），用于把额度
    算成「本次续写最多还能写多少字」。``round_target_ratio`` 与引擎参数同义：
    传了才按目标摊平，不传仍是「上界 - 已写」。
    """
    allocation = round_allocation(
        characters_done, target_characters, round_target_ratio
    )
    upper = allocation["target_range"]["maximum"]
    budget = {
        "characters": allocation["characters"],
        "remaining_characters": max(0, target_characters - characters_done),
        "max_characters": allocation["max_characters"],
        "overshoot_hint": _overshoot_hint(allocation["max_characters"], upper),
        "target_range": dict(allocation["target_range"]),
    }
    if allocation["round_target_ratio"] is not None:
        # 只有显式给了系数才追加审计字段：默认调用的返回结构与 v1.2 一致。
        budget["budget_source"] = allocation["source"]
        budget["formula_characters"] = allocation["formula_characters"]
        budget["round_target_ratio"] = allocation["round_target_ratio"]
    return budget


def _require_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    """校验整数参数范围；布尔值不算整数，避免 True/False 被当成 1/0。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是 {minimum} 到 {maximum} 之间的整数。")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} 必须是 {minimum} 到 {maximum} 之间的整数。")
    return value


def _require_callable(name: str, callback: Any, *, required: bool = False) -> None:
    """校验回调可调用；required 为真时 None 也不合法。"""
    if callback is None:
        if required:
            raise ValueError(f"{name} 必须是可调用对象。")
        return
    if not callable(callback):
        raise ValueError(f"{name} 必须是可调用对象。")


def _round_text(result: Any, round_index: int) -> str:
    """取出并校验单轮生成结果中的正文文本。"""
    if not isinstance(result, Mapping):
        raise ValueError(
            f"第 {round_index} 轮 generate 必须返回字典，实际得到 {type(result).__name__}。"
        )
    text = result.get("text")
    if not isinstance(text, str):
        raise ValueError(f"第 {round_index} 轮 generate 返回的 text 必须是字符串。")
    return text


def _closing_hint(remaining_characters: int) -> str:
    """收束提示：告诉模型只剩少量余量，不要再展开新情节。"""
    return f"只需再写约 {remaining_characters} 字即可收束，不要展开新情节"


def _overshoot_hint(max_characters: int, upper_characters: int) -> str:
    """超冲保护提示：把「本轮最多还能写多少」说成硬数字。

    累计字数必须停在 ``upper_characters``（1.1 倍目标）以内才算落在容差区间，
    因此本轮额度是 ``max_characters = upper_characters - 累计字数``。
    """
    return (
        f"本轮最多再写 {max_characters} 字，累计不要超过 {upper_characters} 字"
        f"（目标 +10% 上限），超出即越界。"
    )


def _stall_retry_hint(characters: int, remaining_characters: int) -> str:
    """v1.0/v1.1 的停滞重试提示：只说「继续写」，不改变展开方式。

    v1.2 默认改用 ``_stall_strategy_hint``；本函数保留给 v1.0/v1.1 语义锁定。
    """
    return (
        f"上一轮只写了 {characters} 字，请继续推进情节，"
        f"本轮写满约 {remaining_characters} 字。"
    )


def _stall_strategy_hint(
    characters: int,
    min_round_gain: int,
    min_characters: int,
    attempt: int,
) -> str:
    """v1.2 停滞换策略提示：换一种展开方式，并给出可核对的最少字数。"""
    strategy = STALL_STRATEGIES[attempt % len(STALL_STRATEGIES)]
    return (
        f"上一轮只写了 {characters} 字，低于单轮有效增量下限 {min_round_gain} 字。"
        f"请改变展开方式：{strategy}；不要重复上一段的内容，"
        f"本次至少写 {min_characters} 字。"
    )


def _pace_hint(
    min_characters: int, deficit_characters: int, rounds_left: int
) -> str:
    """v1.2 停滞恢复期的配额提示：把「离下限还差多少」摊到剩余轮数上。"""
    return (
        f"当前距达标下限还差 {deficit_characters} 字，剩余 {rounds_left} 轮。"
        f"停滞恢复期每轮都要写够配额：本轮至少写 {min_characters} 字，"
        f"写完再收束。"
    )


def _stall_min_characters(
    remaining_characters: int,
    rounds_left: int,
    min_round_gain: int,
    attempt: int,
    deficit_characters: int | None = None,
) -> int:
    """算出停滞重试轮「至少要写多少字」。

    ``deficit_characters`` 是距**达标下限**的缺口（v1.2 用于停滞恢复期）：
    把缺口按剩余轮数摊平（向上取整），生成侧只要每轮写满配额，缺口就会
    线性收敛到 0；不给缺口时退回「按剩余额度摊平」的 v1.2 早期口径。
    再按第几次重试放大，上限是剩余额度本身——既不会要求超过目标，
    也不会低于 ``min_round_gain``。
    """
    if remaining_characters <= 0:
        return 0
    rounds_left = max(1, rounds_left)
    basis = (
        remaining_characters
        if deficit_characters is None
        else max(0, deficit_characters)
    )
    if basis <= 0:
        return 0
    needed = -(-basis // rounds_left)  # ceil
    target = needed * (attempt + 1)
    return max(min_round_gain, min(remaining_characters, target))


def _default_next_context(
    previous_context: Any,
    record: Mapping[str, Any],
    *,
    total_characters: int,
    target_characters: int,
    next_round_index: int,
    upper_characters: int,
    min_round_gain: int,
    rounds_left: int,
    closing_hint: bool = True,
    overshoot_guard: bool = True,
    stall_strategy: bool = True,
    round_target_ratio: float | None = None,
    retry_hint: str | None = None,
    retry_attempt: int = 0,
    retry_min_characters: int | None = None,
    pace_min_characters: int | None = None,
    pace_deficit_characters: int | None = None,
) -> dict[str, Any]:
    """缺省上下文：累积已生成文本，并附带下一轮所需的记账信息。

    - 剩余字数低于 ``CLOSING_HINT_THRESHOLD`` 时注入 ``closing_hint``（可关闭）。
    - ``overshoot_guard`` 打开时，每轮都注入 ``max_characters`` 与
      ``overshoot_hint``：本轮额度 = 1.1 倍上限 - 累计字数。
    - 停滞重试时注入 ``stall_retry_hint``；``stall_strategy`` 打开时还给出
      结构化的 ``stall_retry_min_characters`` / ``stall_retry_attempt``，
      生成侧不必解析中文提示也能拿到「本次至少写多少字」。

    自定义 context_builder 可以完全覆盖该行为（引擎不强制写入这些提示）。
    """
    previous_text = ""
    if isinstance(previous_context, Mapping):
        value = previous_context.get("previous_text")
        if isinstance(value, str):
            previous_text = value
    remaining_characters = max(0, target_characters - total_characters)
    context: dict[str, Any] = {
        "previous_text": previous_text + str(record["text"]),
        "round_index": next_round_index,
        "characters": total_characters,
        "remaining_characters": remaining_characters,
    }
    if overshoot_guard:
        allocation = round_allocation(
            total_characters, target_characters, round_target_ratio
        )
        context["max_characters"] = allocation["max_characters"]
        context["overshoot_hint"] = _overshoot_hint(
            allocation["max_characters"], upper_characters
        )
        if allocation["round_target_ratio"] is not None:
            # opt-in 口径：把额度来源一起下发，生成侧与日志都能审计。
            context["budget_source"] = allocation["source"]
            context["round_target_ratio"] = allocation["round_target_ratio"]
    if closing_hint and 0 < remaining_characters < CLOSING_HINT_THRESHOLD:
        context["closing_hint"] = _closing_hint(remaining_characters)
    if retry_hint is not None:
        context["stall_retry_hint"] = retry_hint
        if stall_strategy:
            context["stall_retry_attempt"] = retry_attempt
            if retry_min_characters is not None:
                context["stall_retry_min_characters"] = retry_min_characters
    if stall_strategy and pace_min_characters is not None:
        # 停滞恢复期：不是重试轮也要带配额，否则「正常轮 30 字 + 重试轮 250 字」
        # 的锯齿永远追不上下限。
        context["min_characters"] = pace_min_characters
        if pace_deficit_characters is not None:
            context["deficit_characters"] = pace_deficit_characters
            context["pace_hint"] = _pace_hint(
                pace_min_characters, pace_deficit_characters, rounds_left
            )
    return context


def _build_next_context(
    context_builder: Callable[[Any, Mapping[str, Any]], Any] | None,
    previous_context: Any,
    record: Mapping[str, Any],
    *,
    total_characters: int,
    target_characters: int,
    next_round_index: int,
    upper_characters: int,
    min_round_gain: int,
    rounds_left: int,
    closing_hint: bool = True,
    overshoot_guard: bool = True,
    stall_strategy: bool = True,
    round_target_ratio: float | None = None,
    retry_hint: str | None = None,
    retry_attempt: int = 0,
    retry_min_characters: int | None = None,
    pace_min_characters: int | None = None,
    pace_deficit_characters: int | None = None,
) -> Any:
    """构造下一轮上下文：缺省实现带提示，自定义实现拿到含 retry_hint 的记录。"""
    if context_builder is None:
        return _default_next_context(
            previous_context,
            record,
            total_characters=total_characters,
            target_characters=target_characters,
            next_round_index=next_round_index,
            upper_characters=upper_characters,
            min_round_gain=min_round_gain,
            rounds_left=rounds_left,
            closing_hint=closing_hint,
            overshoot_guard=overshoot_guard,
            stall_strategy=stall_strategy,
            round_target_ratio=round_target_ratio,
            retry_hint=retry_hint,
            retry_attempt=retry_attempt,
            retry_min_characters=retry_min_characters,
            pace_min_characters=pace_min_characters,
            pace_deficit_characters=pace_deficit_characters,
        )
    builder_record = dict(record)
    if round_target_ratio is not None:
        allocation = round_allocation(
            total_characters, target_characters, round_target_ratio
        )
        builder_record["max_characters"] = allocation["max_characters"]
        builder_record["budget_source"] = allocation["source"]
        builder_record["round_target_ratio"] = allocation["round_target_ratio"]
    if retry_hint is not None:
        builder_record["retry_hint"] = retry_hint
        builder_record["retry_attempt"] = retry_attempt
        if retry_min_characters is not None:
            builder_record["retry_min_characters"] = retry_min_characters
    if stall_strategy and pace_min_characters is not None:
        # 恢复期配额同样交给自定义 builder：调用方不必解析中文提示。
        builder_record["min_characters"] = pace_min_characters
        if pace_deficit_characters is not None:
            builder_record["deficit_characters"] = pace_deficit_characters
    return context_builder(previous_context, builder_record)


def run_budgeted_continuation(
    generate: Callable[[Any, int], Mapping[str, Any]],
    *,
    target_characters: int = DEFAULT_TARGET_CHARACTERS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    min_round_gain: int = DEFAULT_MIN_ROUND_GAIN,
    max_stall_retries: int = DEFAULT_MAX_STALL_RETRIES,
    hard_max_rounds: int = DEFAULT_HARD_MAX_ROUNDS,
    closing_hint: bool = True,
    overshoot_guard: bool = True,
    stall_strategy: bool = True,
    round_target_ratio: float | None = None,
    seed_text: str = "",
    budget_check: Callable[[int], Any] | None = None,
    should_cancel: Callable[[], Any] | None = None,
    context_builder: Callable[[Any, Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """在预算内执行多轮续写，返回候选文本与停止原因。

    引擎只负责循环与记账，不关心模型、数据库和 HTTP：

    - ``generate(prompt_context, round_index)``：调用方注入的生成函数，``round_index``
      从 1 开始（重试轮也占用轮次编号）。首轮 ``prompt_context`` 为空字典（唯一
      例外：给了 ``seed_text`` 时，首轮上下文由 seed 装配，带 ``previous_text``），
      后续轮次由 ``context_builder`` 生成。返回值必须是包含 ``text`` 字符串的
      映射；额外字段会被忽略，以保证返回值始终可 JSON 序列化。
    - ``budget_check(round_index)``：每轮开始前调用，返回假值表示预算耗尽，
      停止原因为 ``BUDGET_EXHAUSTED``；缺省视为预算充足。
    - ``should_cancel()``：每轮开始前调用，返回真值表示作者取消，停止原因为
      ``CANCELED``。取消优先于预算检查。
    - ``context_builder(previous_context, round_record)``：一轮生成后、确认还要
      继续时调用，返回值原样作为下一轮的 ``prompt_context``。缺省实现会把已生成
      文本累积到 ``{"previous_text": ...}``，并附带 ``round_index``、``characters``、
      ``remaining_characters``；剩余不足 600 字时额外附带 ``closing_hint``；
      停滞重试时额外附带 ``stall_retry_hint``。自定义实现时这些提示不会强制注入，
      但重试轮的记录会带 ``retry_hint`` 字段供调用方自行取用。

    停滞重试：某轮增量低于 ``min_round_gain`` 时，最多重试 ``max_stall_retries``
    次（默认 1 次；调到 2 次以上会逐次换策略，但每次多一次模型调用）。重试轮计入 ``rounds`` 并标记 ``retried: true``；重试后仍
    低于阈值才判 ``NO_PROGRESS``。

    停滞换策略（``stall_strategy``，默认开）：重试不重复同一句「请继续写」，
    而是每次换一种展开方式，并给出「本次至少写 N 字」（N 按剩余额度与剩余
    轮数摊平后逐次放大）。总轮数仍受 ``hard_max_rounds`` 硬约束。

    超冲保护（``overshoot_guard``，默认开）：每轮上下文带上本轮额度
    ``max_characters = floor(1.1×目标) - 累计字数`` 与 ``overshoot_hint``，
    让本轮就守住上界；生成侧不服从时如实记录 ``within_tolerance=False`` 与
    ``overshoot_guard``，**不裁剪正文**。

    弹性轮数：到达 ``max_rounds`` 时，若累计进度 ≥ 目标 60% 且最近一轮增量
    ≥ ``min_round_gain``，允许继续生成到 ``hard_max_rounds``（默认 12，必须
    ≥ ``max_rounds``）；否则按原逻辑停止。停止原因仍为 ``MAX_ROUNDS``。

    断点续写（``seed_text``，默认空串）：调用方把「已经写出来的正文」传进来，
    引擎把它当成第 0 轮——``characters`` 从 ``count_characters(seed_text)`` 起算，
    ``remaining_characters`` 相应减少，第 1 轮上下文带 ``previous_text=seed_text``，
    返回正文是 ``seed_text`` 与各轮新生成文本的原样拼接（不裁剪、不改写）。
    ``seed_text`` 只改变起点，不改变 ``target_range`` 与 ``within_tolerance`` 的
    定义；``rounds`` / ``round_metrics`` 仍只统计本次新发生的轮次。seed 已达
    ``target_range`` 下限时不再调度任何轮次（provider 调用数为 0），直接返回
    ``TARGET_REACHED``。

    每轮生成后的停止判定顺序：
    累计字数达到目标下限 → ``TARGET_REACHED``；
    增量不足且重试预算用尽 → ``NO_PROGRESS``；
    到达轮数上限（含弹性扩展后的 ``hard_max_rounds``）→ ``MAX_ROUNDS``。

    作者控制：返回值只是候选，不写正文、不写 canon；调用方必须交给候选箱，
    由作者决定是否采纳。

    字符口径：非空白 Unicode 字符（标点计入、空白不计），与
    ``moyu_output.count_characters`` 一致。目标容差为目标字数的 ±10%，
    即 ``[ceil(0.9×目标), floor(1.1×目标)]`` 视为达标；累计字数达到下限即停止，
    因此可能略超上限，此时 ``within_tolerance`` 为假（本模块不裁剪正文）。

    ``target_characters`` 指本次多轮续写累计生成的字数。若调用方希望把已有正文
    计入整章目标，应先自行扣减正文长度再传入（本引擎不读取、也不修改正文）。

    参数：
        generate: 生成函数，见上。
        target_characters: 目标总字数，取值 500–20000。
        max_rounds: 名义最多生成轮数，取值 1–12。
        min_round_gain: 单轮最小有效增量，低于该值触发停滞重试。
        max_stall_retries: 每次停滞最多重试次数，取值 0–12。
        hard_max_rounds: 弹性轮数硬上限，取值 1–24 且不小于 ``max_rounds``。
        closing_hint: 是否注入余量收束提示（v1.0/v1.1 语义锁定用，默认开）。
        overshoot_guard: 是否每轮注入本轮字数额度与超冲提示（默认开）。
        stall_strategy: 停滞重试是否换策略并给出最少字数（默认开；
            关闭时退回 v1.1 的「请继续推进情节」提示）。
        round_target_ratio: 可选口径（默认 None = 旧行为）。给了就把每轮额度从
            ``upper - 累计`` 改成 ``clamp(ceil((target - 累计) / ratio), 50,
            upper - 累计)``；每轮额度与来源会写进返回的 ``budget_plan``。
        seed_text: 断点续写起点（默认空串 = 旧行为）。已写出的正文由调用方传入，
            引擎从它的字数起算，并把它作为第 1 轮的 ``previous_text``；纯空白
            （``count_characters`` 为 0）视同空串。正文原样拼接，不裁剪。
        budget_check: 可选预算检查，见上。
        should_cancel: 可选取消检查，见上。
        context_builder: 可选下一轮上下文构造器，见上。

    返回：
        dict，包含 ``text``（各轮以空行拼接的候选正文）、``characters``（非空白字符
        总数）、``target_characters``、``target_range``、``target_reached``、
        ``within_tolerance``、``stop_reason``、``round_count``、``retries``
        （实际发生的重试轮数）、``extended_rounds``（超过 ``max_rounds`` 后实际
        生成的轮数）、``max_rounds``、``hard_max_rounds``、``min_round_gain``、
        ``max_stall_retries``、``counting``、``candidate_only``、``rounds``，
        以及 v1.2 的可解释字段 ``closing_hint``（开关值）、
        ``overshoot_guard``（``enabled`` / ``upper_characters`` / ``hint_rounds``
        / ``characters_over_upper``）、``stall_strategy``、``stall_hints``
        （每次停滞重试的轮次、字数、策略序号与最少字数），以及可选口径的
        审计字段 ``round_target_ratio`` 与 ``budget_plan``（每轮的
        ``max_characters`` / ``source`` / ``delivered`` / ``characters_before``）；
        断点续写时还有 ``seed_characters``（seed 计入的字数，未给 seed 时为 0）。

    异常：
        ValueError：参数越界、回调不可调用，或 ``generate`` 返回值不符合约定。
        回调自身抛出的异常会原样向上传播，便于调用方定位真实模型错误。
    """
    target_characters = _require_int(
        "target_characters", target_characters, *TARGET_CHARACTERS_LIMITS
    )
    max_rounds = _require_int("max_rounds", max_rounds, *MAX_ROUNDS_LIMITS)
    hard_max_rounds = _require_int(
        "hard_max_rounds", hard_max_rounds, *HARD_MAX_ROUNDS_LIMITS
    )
    if hard_max_rounds < max_rounds:
        raise ValueError("hard_max_rounds 必须大于或等于 max_rounds。")
    max_stall_retries = _require_int(
        "max_stall_retries", max_stall_retries, *MAX_STALL_RETRIES_LIMITS
    )
    if isinstance(min_round_gain, bool) or not isinstance(min_round_gain, int):
        raise ValueError("min_round_gain 必须是大于或等于 0 的整数。")
    if min_round_gain < 0:
        raise ValueError("min_round_gain 必须是大于或等于 0 的整数。")
    for name, value in (
        ("closing_hint", closing_hint),
        ("overshoot_guard", overshoot_guard),
        ("stall_strategy", stall_strategy),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{name} 必须是布尔值（True/False）。")
    round_target_ratio = _require_round_target_ratio(round_target_ratio)
    if not isinstance(seed_text, str):
        raise ValueError("seed_text 必须是字符串（断点续写的已有正文）。")

    _require_callable("generate", generate, required=True)
    _require_callable("budget_check", budget_check)
    _require_callable("should_cancel", should_cancel)
    _require_callable("context_builder", context_builder)

    lower, upper = target_range(target_characters)
    extension_floor = _extension_threshold(target_characters)
    # 断点续写：seed 只改变起点。字数口径复用 count_characters，
    # 纯空白 seed 的计数为 0，按「没有 seed」处理（不把空白当正文拼接）。
    seed_characters = count_characters(seed_text)
    effective_seed = seed_text if seed_characters > 0 else ""
    rounds: list[dict[str, Any]] = []
    pieces: list[str] = [effective_seed] if effective_seed else []
    total_characters = seed_characters
    stop_reason = MAX_ROUNDS  # 兜底；正常路径都会在循环内被覆盖
    prompt_context: Any = {}
    retries = 0
    extended_rounds = 0
    stall_retries_used = 0
    extension_active = False
    retry_pending = False
    overshoot_hint_rounds: list[int] = []
    stall_hints: list[dict[str, Any]] = []
    # 逐轮额度审计：每轮实际下发的额度、它的来源，以及是否真的送达生成侧。
    budget_plan: list[dict[str, Any]] = []
    # 停滞恢复期：一旦出现停滞，直到追平下限为止，每轮都带配额。
    recovery_active = False

    def _next_context(
        record: Mapping[str, Any],
        round_index: int,
        *,
        retry_hint: str | None = None,
        retry_attempt: int = 0,
        retry_min_characters: int | None = None,
    ) -> Any:
        """统一构造下一轮上下文，把 v1.2 的新参数都带上。"""
        rounds_left = max(1, hard_max_rounds - round_index)
        pace_min_characters: int | None = None
        pace_deficit_characters: int | None = None
        if stall_strategy and recovery_active and total_characters < lower:
            pace_deficit_characters = lower - total_characters
            pace_min_characters = _stall_min_characters(
                max(0, target_characters - total_characters),
                rounds_left,
                min_round_gain,
                0,
                deficit_characters=pace_deficit_characters,
            )
        return _build_next_context(
            context_builder,
            prompt_context,
            record,
            total_characters=total_characters,
            target_characters=target_characters,
            next_round_index=round_index + 1,
            upper_characters=upper,
            min_round_gain=min_round_gain,
            rounds_left=rounds_left,
            closing_hint=closing_hint,
            overshoot_guard=overshoot_guard,
            stall_strategy=stall_strategy,
            round_target_ratio=round_target_ratio,
            retry_hint=retry_hint,
            retry_attempt=retry_attempt,
            retry_min_characters=retry_min_characters,
            pace_min_characters=pace_min_characters,
            pace_deficit_characters=pace_deficit_characters,
        )

    if effective_seed:
        # 断点续写：把 seed 当成「第 0 轮」，用同一套装配逻辑生成第 1 轮提示，
        # 这样 previous_text 与 seed_text 逐字相等，remaining_characters 也已扣减。
        # 空 seed 时 prompt_context 仍是空字典（与旧行为逐字节一致）。
        seed_record: dict[str, Any] = {
            "round_index": 0,
            "text": effective_seed,
            "characters": seed_characters,
            "cumulative_characters": seed_characters,
        }
        prompt_context = _next_context(seed_record, 0)

    for round_index in range(1, hard_max_rounds + 1):
        # 断点续写：seed 已达下限时不再调度任何轮次（provider 调用数为 0）。
        # 放在取消/预算检查之前：已经写成的东西不需要再问一遍「要不要写」。
        if seed_characters >= lower:
            stop_reason = TARGET_REACHED
            break
        # 先检查作者取消，再检查预算：作者中止优先于任何预算判断。
        if should_cancel is not None and should_cancel():
            stop_reason = CANCELED
            break
        if budget_check is not None and not budget_check(round_index):
            stop_reason = BUDGET_EXHAUSTED
            break

        # 超冲保护是「生成前」的：本轮上下文里已经带了额度，这里只记录它真的送达了。
        if (
            overshoot_guard
            and isinstance(prompt_context, Mapping)
            and "overshoot_hint" in prompt_context
        ):
            overshoot_hint_rounds.append(round_index)

        # 额度审计：记下本轮真实下发的额度与它的来源；没送达就记下本该下发的值。
        planned = round_allocation(
            total_characters, target_characters, round_target_ratio
        )
        entry: dict[str, Any] = {
            "round_index": round_index,
            "characters_before": total_characters,
            "planned_max_characters": planned["max_characters"],
        }
        delivered = isinstance(prompt_context, Mapping) and "max_characters" in prompt_context
        if delivered:
            entry["max_characters"] = prompt_context["max_characters"]
            entry["source"] = prompt_context.get(
                "budget_source", "ratio" if round_target_ratio is not None else "upper"
            )
            entry["delivered"] = True
        else:
            # 首轮上下文按契约是空字典：额度只能由调用方合入开场提示。
            entry["max_characters"] = planned["max_characters"]
            entry["source"] = planned["source"]
            entry["delivered"] = False
        budget_plan.append(entry)

        text = _round_text(generate(prompt_context, round_index), round_index)
        characters = count_characters(text)
        total_characters += characters
        record: dict[str, Any] = {
            "round_index": round_index,
            "text": text,
            "characters": characters,
            "cumulative_characters": total_characters,
        }
        if retry_pending:
            record["retried"] = True
            record["retry_attempt"] = stall_retries_used
            retries += 1
        rounds.append(record)
        pieces.append(text)
        if round_index > max_rounds:
            extended_rounds += 1

        if total_characters >= lower:
            stop_reason = TARGET_REACHED
            break

        if characters < min_round_gain:
            remaining_characters = max(0, target_characters - total_characters)
            if stall_retries_used < max_stall_retries and round_index < hard_max_rounds:
                attempt = stall_retries_used
                stall_retries_used += 1
                retry_pending = True
                # 进入停滞恢复期：配额按「距下限的缺口 / 剩余轮数」算，
                # 恢复期内每轮都带同一个口径的配额，直到追平下限。
                recovery_active = True
                min_characters = _stall_min_characters(
                    remaining_characters,
                    hard_max_rounds - round_index,
                    min_round_gain,
                    attempt,
                    deficit_characters=max(0, lower - total_characters),
                )
                if stall_strategy:
                    hint = _stall_strategy_hint(
                        characters, min_round_gain, min_characters, attempt
                    )
                else:
                    hint = _stall_retry_hint(characters, remaining_characters)
                stall_hints.append(
                    {
                        "round_index": round_index,
                        "characters": characters,
                        "attempt": attempt + 1,
                        "strategy": (
                            STALL_STRATEGIES[attempt % len(STALL_STRATEGIES)]
                            if stall_strategy
                            else None
                        ),
                        "min_characters": min_characters if stall_strategy else None,
                        "hint": hint,
                    }
                )
                prompt_context = _next_context(
                    record,
                    round_index,
                    retry_hint=hint,
                    retry_attempt=attempt + 1,
                    retry_min_characters=min_characters if stall_strategy else None,
                )
                continue
            stop_reason = NO_PROGRESS
            break

        # 本轮有有效增量：重置停滞重试计数，并决定是否进入弹性轮数。
        stall_retries_used = 0
        retry_pending = False

        if round_index >= hard_max_rounds:
            stop_reason = MAX_ROUNDS
            break

        if round_index >= max_rounds and not extension_active:
            # 停滞恢复期不受「已写够 60%」门控：配额是定向给到下限的，
            # 但总量仍受 hard_max_rounds 硬约束。
            if total_characters >= extension_floor or (
                stall_strategy and recovery_active
            ):
                extension_active = True
            else:
                stop_reason = MAX_ROUNDS
                break

        prompt_context = _next_context(record, round_index)

    return {
        "text": "\n\n".join(pieces),
        "characters": total_characters,
        "seed_characters": seed_characters,
        "target_characters": target_characters,
        "target_range": {"minimum": lower, "maximum": upper},
        "target_reached": total_characters >= lower,
        "within_tolerance": lower <= total_characters <= upper,
        "stop_reason": stop_reason,
        "round_count": len(rounds),
        "retries": retries,
        "extended_rounds": extended_rounds,
        "max_rounds": max_rounds,
        "hard_max_rounds": hard_max_rounds,
        "min_round_gain": min_round_gain,
        "max_stall_retries": max_stall_retries,
        "closing_hint": closing_hint,
        "overshoot_guard": {
            "enabled": overshoot_guard,
            "upper_characters": upper,
            "hint_rounds": list(overshoot_hint_rounds),
            "characters_over_upper": max(0, total_characters - upper),
        },
        "stall_strategy": stall_strategy,
        "stall_hints": list(stall_hints),
        "round_target_ratio": round_target_ratio,
        "budget_plan": list(budget_plan),
        "counting": COUNTING_RULE,
        "candidate_only": True,
        "rounds": rounds,
    }
