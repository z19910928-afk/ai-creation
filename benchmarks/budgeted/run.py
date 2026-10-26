#!/usr/bin/env python3
"""Q1-5b：多轮字数预算续写的一次通过率基准。

用内存 fake provider 驱动 ``backend.moyu_continuation.run_budgeted_continuation``，
在 20 个章节场景上统计一次通过率、轮数、停止原因分布与耗时，并验证引擎
不会改写正文（每次运行前后正文 sha256 完全一致）。

一次通过的定义（与策略文档 Q1 一致，不放宽）：

    ``stop_reason == TARGET_REACHED`` 且 ``within_tolerance`` 为真

即累计字数落在 ``[ceil(0.9×目标), floor(1.1×目标)]`` 内。引擎在达到下限
（0.9 倍目标）时就停止，所以单轮冲过头、越过 1.1 倍上限时 ``within_tolerance``
为假，**算失败**，不能只看 ``target_reached``。

运行::

    .venv/bin/python benchmarks/budgeted/run.py --seed 20261023

输出::

    benchmarks/budgeted/results/budgeted-baseline.json   确定性核心报告
    benchmarks/budgeted/results/budgeted-baseline.md     人类可读摘要
    benchmarks/budgeted/results/budgeted-timing.json     墙钟耗时（非确定性，单列）
    docs/consistency-budget-baseline-2026-10-23.md       正式基线文档

设计约束：

- 不访问网络、不调用真实模型、不读写数据库；只用内存 fake。
- 确定性核心不含墙钟时间；同一 seed 重复运行逐字节一致，并输出 digest 供外部核对。
- **基准成功 ≠ 指标达标**：退出码只反映「基准完整性验收」，一次通过率低于 80%
  只记录为 ``q1_target_met = false`` 并如实报告，不会让命令失败——更不会为了
  凑数字去调场景配比。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.moyu_continuation import (  # noqa: E402
    BUDGET_EXHAUSTED,
    CANCELED,
    DEFAULT_MAX_ROUNDS,
    DEFAULT_MIN_ROUND_GAIN,
    MAX_ROUNDS,
    NO_PROGRESS,
    OVERSHOOT,
    STOP_REASONS,
    TARGET_CHARACTERS_LIMITS,
    TARGET_REACHED,
    TARGET_TOLERANCE,
    round_budget,
    run_budgeted_continuation,
    target_range,
)
from backend.moyu_output import COUNTING_RULE, count_characters  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "results" / "budgeted-baseline.json"
DEFAULT_MD = HERE / "results" / "budgeted-baseline.md"
DEFAULT_TIMING = HERE / "results" / "budgeted-timing.json"
DEFAULT_DOC = ROOT / "docs" / "consistency-budget-baseline-2026-10-23.md"

BENCHMARK_NAME = "budgeted-continuation-first-pass"
BENCHMARK_VERSION = "1.0"
BASELINE_DATE = "2026-10-23"
Q1_TARGET_CHARACTERS = 3000
Q1_TARGET_RATE = 0.80

# v1.1 复测的产物路径（沿用 v1 的命名风格，不改动 v1 基线文件）。
V11_OUT = HERE / "results" / "budgeted-v1.1-baseline.json"
V11_MD = HERE / "results" / "budgeted-v1.1-baseline.md"
V11_TIMING = HERE / "results" / "budgeted-v1.1-timing.json"
V11_DOC = ROOT / "docs" / "consistency-budget-v1.1-2026-10-23.md"

# v1.2 复测的产物路径（同样不动 v1.0/v1.1 已发布的文件）。
V12_OUT = HERE / "results" / "budgeted-v1.2-baseline.json"
V12_MD = HERE / "results" / "budgeted-v1.2-baseline.md"
V12_TIMING = HERE / "results" / "budgeted-v1.2-timing.json"
V12_DOC = ROOT / "docs" / "consistency-budget-v1.2-2026-10-23.md"

# 引擎变体：把「v1.0 语义」与「v1.1 语义」都写成显式参数。
#
# 关键：v1.0 的默认值在 v1.1 里变成了 max_stall_retries=1 / hard_max_rounds=12。
# 若基准不显式传参，同一个 run.py 会在引擎升级后静默改变行为，
# 已发布的 v1 基线就不可复现了。因此这里把两套语义都钉死。
#
# hard_max_rounds=None 表示「取该场景的 max_rounds」，即禁用弹性轮数。
ENGINE_VARIANTS: dict[str, dict] = {
    "v1": {
        "label": "v1.0（锁定参数）",
        "max_stall_retries": 0,
        "hard_max_rounds": None,
        # t14 的 closing_hint 默认开启后，v1 臂必须把它也钉住，否则「v1.0 复现」
        # 只复现了轮数语义，提示注入却跟着新引擎漂移（v1.2 修掉的已知缺陷）。
        "closing_hint": False,
        "overshoot_guard": False,
        "stall_strategy": False,
        "engine_version": "1.0",
    },
    "v1.1": {
        "label": "v1.1（锁定参数）",
        "max_stall_retries": 1,
        "hard_max_rounds": 12,
        "closing_hint": True,
        "overshoot_guard": False,
        "stall_strategy": False,
        "engine_version": "1.1",
    },
    "v1.2": {
        "label": "v1.2（引擎默认）",
        "max_stall_retries": 1,
        "hard_max_rounds": 12,
        "closing_hint": True,
        "overshoot_guard": True,
        "stall_strategy": True,
        "engine_version": "1.2",
    },
}
VARIANT_ORDER = ("v1", "v1.1", "v1.2")


# --------------------------------------------------------------------------- #
# 质量档位：fake provider 的产出模型
# --------------------------------------------------------------------------- #

TIERS: dict[str, dict] = {
    "calibrated": {
        "label": "一次到位",
        "ratio": (1.00, 0.05),
        "yield_note": "单轮产出 ≈ 剩余额度的 100%±5%",
        "behaviour": "首轮即达标",
    },
    "steady": {
        "label": "渐进收敛",
        "ratio": (0.50, 0.08),
        "yield_note": "单轮产出 ≈ 剩余额度的 50%±8%",
        "behaviour": "3–5 轮收敛",
    },
    "cautious": {
        "label": "保守欠量",
        "ratio": (0.38, 0.04),
        "yield_note": "单轮产出 ≈ 剩余额度的 38%±4%",
        "behaviour": "5–6 轮勉强收敛，贴近 max_rounds 上限",
    },
    "volatile": {
        "label": "无视预算",
        "ratio": (0.75, 0.50),
        "yield_note": "单轮产出 ≈ 剩余额度的 75%±50%（可超过 100%）",
        "behaviour": "常首轮超冲，越过 +10% 上限",
    },
    "underpowered": {
        "label": "产出不足",
        "ratio": (0.18, 0.03),
        "yield_note": "单轮产出 ≈ 剩余额度的 18%±3%",
        "behaviour": "6 轮仍达不到下限",
    },
    "stalling": {
        "label": "停滞",
        "fixed_yield": (12, 30),
        "yield_note": "单轮固定 12–30 字，低于 min_round_gain=50",
        "behaviour": "首轮即判 NO_PROGRESS",
    },
    "budget_limited": {
        "label": "预算受限",
        "ratio": (0.50, 0.08),
        "yield_note": "产出同 steady，但预算在第 3 轮开始前耗尽",
        "behaviour": "BUDGET_EXHAUSTED",
    },
    "canceled": {
        "label": "作者取消",
        "ratio": (0.50, 0.08),
        "yield_note": "产出同 steady，但第 2 轮开始前作者取消",
        "behaviour": "CANCELED",
    },
}

TIER_ORDER = (
    "calibrated",
    "steady",
    "cautious",
    "volatile",
    "underpowered",
    "stalling",
    "budget_limited",
    "canceled",
)

# 生成侧是否「合格」的分组。分组只用于分层解读，不改变总体口径。
QUALIFIED_TIERS = ("calibrated", "steady", "cautious")
DEFICIENT_TIERS = ("volatile", "underpowered", "stalling")
INTERRUPTED_TIERS = ("budget_limited", "canceled")

STOP_REASON_LABELS = {
    TARGET_REACHED: "达标停止",
    NO_PROGRESS: "产出停滞",
    MAX_ROUNDS: "轮数耗尽",
    BUDGET_EXHAUSTED: "预算耗尽",
    CANCELED: "作者取消",
    OVERSHOOT: "超冲停止",
}

FAILURE_CLASS_LABELS = {
    "overshoot": "超冲（越过 +10% 上限）",
    NO_PROGRESS: "产出停滞（单轮增量 < min_round_gain）",
    MAX_ROUNDS: "轮数耗尽仍未达标",
    BUDGET_EXHAUSTED: "预算中断（外部原因）",
    CANCELED: "作者取消（外部原因）",
}


# --------------------------------------------------------------------------- #
# 场景矩阵（20 个）。配比是**假设**，不是从真实模型测出来的，见 README。
# --------------------------------------------------------------------------- #

SCENARIO_TABLE = (
    ("sc-01", 2500, "calibrated"),
    ("sc-02", 3000, "calibrated"),
    ("sc-03", 3000, "calibrated"),
    ("sc-04", 4000, "calibrated"),
    ("sc-05", 2500, "steady"),
    ("sc-06", 2500, "steady"),
    ("sc-07", 3000, "steady"),
    ("sc-08", 3000, "steady"),
    ("sc-09", 4000, "steady"),
    ("sc-10", 4000, "steady"),
    ("sc-11", 2500, "cautious"),
    ("sc-12", 3000, "cautious"),
    ("sc-13", 4000, "cautious"),
    ("sc-14", 3000, "volatile"),
    ("sc-15", 4000, "volatile"),
    ("sc-16", 2500, "underpowered"),
    ("sc-17", 3000, "underpowered"),
    ("sc-18", 3000, "stalling"),
    ("sc-19", 3000, "budget_limited"),
    ("sc-20", 4000, "canceled"),
)


# --------------------------------------------------------------------------- #
# 中文语料：只用于生成「看起来像正文」的定长文本，不含空白字符
# --------------------------------------------------------------------------- #

_SENTENCES = (
    "夜色沿着山脊漫下来，风把松针吹得簌簌作响。",
    "他握紧刀柄，指节因用力而泛白。",
    "远处传来更鼓声，一下一下敲在人心上。",
    "雪落在青石板上，转瞬化成一层薄薄的水光。",
    "她抬头看了一眼檐角的灯笼，没有说话。",
    "剑气掠过，庭中的枯叶被齐齐削成两半。",
    "炉火将熄，炭灰里还留着一点暗红。",
    "山道陡峭，马蹄踏碎了一路的薄冰。",
    "他把那封信折好，塞进衣襟最里层。",
    "廊下的铜铃被风撞响，声音清冷而绵长。",
    "药炉上白雾翻涌，苦涩的气味漫了满屋。",
    "她数着自己的呼吸，等到第七下才推门。",
    "刀锋停在半空，映出一双极静的眼睛。",
    "旧城墙的砖缝里长出了半人高的荒草。",
    "雨点砸在船篷上，密得像有人在擂鼓。",
    "他忽然停步，侧耳听了片刻，又继续往前。",
    "烛火晃了一下，墙上的影子跟着歪斜。",
    "那人袖口沾着血，神色却平静得近乎温和。",
    "晨雾未散，渡口的木桩只露出半截。",
    "她把剑横在膝上，慢慢擦去刃口的锈迹。",
    "山谷里回声来回撞了几次，才渐渐平息。",
    "案上的茶已经凉透，浮着一层薄薄的油光。",
    "他伸手探了探井口的石沿，指尖一片湿冷。",
    "风从窗缝里钻进来，吹得书页哗哗作响。",
    "街市散得很快，只剩下几盏孤零零的灯。",
    "她低头笑了笑，把袖中的短刃推回去半寸。",
    "崖边的松树被雷劈过，焦黑的枝干仍立着。",
    "他记不清自己走了多久，只觉得腿脚发沉。",
    "铜盆里的水映着天光，微微荡开一圈涟漪。",
    "远处有人在唱一支很老的调子，词句模糊。",
)


def _build_corpus() -> str:
    """把句子循环拼成足够长的语料；保证不含任何空白字符。"""
    pieces: list[str] = []
    total = 0
    index = 0
    while total < 16_000:
        sentence = _SENTENCES[index % len(_SENTENCES)]
        pieces.append(sentence)
        total += len(sentence)
        index += 1
    corpus = "".join(pieces)
    if any(character.isspace() for character in corpus):
        raise AssertionError("语料不能包含空白字符，否则字数口径会失真。")
    return corpus


_CORPUS = _build_corpus()


def _slice_text(length: int, rng: random.Random) -> str:
    """从语料里取恰好 ``length`` 个非空白字符的片段（允许环绕）。"""
    if length <= 0:
        return ""
    if length > len(_CORPUS):
        raise ValueError(f"请求长度 {length} 超过语料长度 {len(_CORPUS)}。")
    start = rng.randrange(len(_CORPUS))
    if start + length <= len(_CORPUS):
        return _CORPUS[start : start + length]
    return (_CORPUS + _CORPUS)[start : start + length]


# --------------------------------------------------------------------------- #
# fake provider
# --------------------------------------------------------------------------- #


class FakeProvider:
    """按质量档位产出定长中文正文的内存 fake provider。

    只依赖传入的 ``random.Random``，因此同一 seed 下逐字可复现。
    """

    def __init__(self, tier: str, target_characters: int, rng: random.Random) -> None:
        if tier not in TIERS:
            raise ValueError(f"未知质量档位：{tier}")
        self.tier = tier
        self.target_characters = target_characters
        self.rng = rng
        self.emitted = 0
        self.calls = 0
        self.generate_seconds = 0.0
        # v1.1 的提示注入是引擎通过 prompt_context 传下来的，provider 能直接看到。
        # 这里只做「观测」，不改变产出——因此对 v1.0 与 v1.1 的对照没有干扰。
        self.closing_hint_rounds = 0
        self.stall_hint_rounds = 0
        self.hint_round_indexes: dict[str, list[int]] = {
            "closing_hint": [],
            "stall_retry_hint": [],
        }
        # v1.2：超冲额度提示与「本次至少写 N 字」也只观测，不改变产出。
        self.overshoot_hint_rounds = 0
        self.overshoot_hint_round_indexes: list[int] = []
        self.stall_min_characters: list[int] = []
        self.max_characters_seen: list[int] = []
        self.obeyed_rounds = 0

    provider_name = "fake"

    def _next_length(self, remaining: int, round_index: int) -> int:
        spec = TIERS[self.tier]
        fixed = spec.get("fixed_yield")
        if fixed is not None:
            low, high = fixed
            return self.rng.randint(low, high)
        mean, spread = spec["ratio"]
        ratio = mean + self.rng.uniform(-spread, spread)
        if remaining <= 0:
            return 1
        return max(1, int(round(remaining * ratio)))

    def _observe(self, prompt_context, round_index: int) -> None:
        """只记录引擎注入了哪些提示，不改变产出（对抗式基线必须对提示无响应）。"""
        if not isinstance(prompt_context, dict):
            return
        if "closing_hint" in prompt_context:
            self.closing_hint_rounds += 1
            self.hint_round_indexes["closing_hint"].append(round_index)
        if "stall_retry_hint" in prompt_context:
            self.stall_hint_rounds += 1
            self.hint_round_indexes["stall_retry_hint"].append(round_index)
        if "overshoot_hint" in prompt_context:
            self.overshoot_hint_rounds += 1
            self.overshoot_hint_round_indexes.append(round_index)
        if "max_characters" in prompt_context:
            self.max_characters_seen.append(prompt_context["max_characters"])
        if "stall_retry_min_characters" in prompt_context:
            self.stall_min_characters.append(
                prompt_context["stall_retry_min_characters"]
            )

    def _apply_hints(
        self, length: int, prompt_context, round_index: int
    ) -> int:
        """对抗式 provider：提示一律不服从，长度原样返回。"""
        return length

    def __call__(self, prompt_context, round_index: int) -> dict:
        started = time.perf_counter()
        self._observe(prompt_context, round_index)
        remaining = max(0, self.target_characters - self.emitted)
        length = self._next_length(remaining, round_index)
        length = self._apply_hints(length, prompt_context, round_index)
        text = _slice_text(length, self.rng)
        self.emitted += len(text)
        self.calls += 1
        self.generate_seconds += time.perf_counter() - started
        # 额外字段会被引擎忽略，用来确认「返回值必须可 JSON 序列化」没有被破坏。
        return {
            "text": text,
            "round_index": round_index,
            "provider": self.provider_name,
            "tier": self.tier,
        }


class ObeyingProvider(FakeProvider):
    """附录专用：假设真实模型**服从**引擎提示的 provider（不计入 first_pass）。

    它读两条提示并按提示决定本轮产出长度：

    - ``max_characters``：本轮最多还能写多少字（超冲保护的额度）；
    - ``stall_retry_min_characters``：本次至少写多少字（停滞换策略的额度）。

    ``obey_first_round_envelope=True`` 时额外模拟「调用方用
    ``round_budget()`` 把额度合进开场提示」：引擎首轮上下文按契约是空字典，
    因此首轮的额度只能由调用方给。
    """

    provider_name = "obeying"

    def __init__(
        self,
        tier: str,
        target_characters: int,
        rng: random.Random,
        *,
        obey_first_round_envelope: bool = False,
    ) -> None:
        super().__init__(tier, target_characters, rng)
        self.obey_first_round_envelope = obey_first_round_envelope
        self.obeyed_rounds = 0

    def _apply_hints(self, length: int, prompt_context, round_index: int) -> int:
        minimum = None
        maximum = None
        if isinstance(prompt_context, dict):
            # 重试轮给 stall_retry_min_characters，恢复期给 min_characters。
            for key in ("stall_retry_min_characters", "min_characters"):
                value = prompt_context.get(key)
                if isinstance(value, int):
                    minimum = value if minimum is None else max(minimum, value)
            value = prompt_context.get("max_characters")
            if isinstance(value, int):
                maximum = value
        if maximum is None and round_index == 1 and self.obey_first_round_envelope:
            # 模拟调用方把 round_budget(0, target) 合进首轮开场提示。
            maximum = round_budget(0, self.target_characters)["max_characters"]
        shaped = length
        if minimum is not None:
            shaped = max(shaped, minimum)
        if maximum is not None:
            shaped = min(shaped, maximum)
        shaped = max(1, shaped)
        if shaped != length:
            self.obeyed_rounds += 1
        return shaped


# --------------------------------------------------------------------------- #
# 场景执行
# --------------------------------------------------------------------------- #


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _scenario_rng(seed: int, index: int) -> random.Random:
    """每个场景一条独立的随机流，避免场景间相互影响。"""
    return random.Random(seed * 1000 + index)


def _build_scenarios(variant: str = "v1") -> list[dict]:
    if variant not in ENGINE_VARIANTS:
        raise ValueError(f"未知引擎变体：{variant}")
    engine = ENGINE_VARIANTS[variant]
    scenarios: list[dict] = []
    for index, (scenario_id, target, tier) in enumerate(SCENARIO_TABLE):
        max_rounds = DEFAULT_MAX_ROUNDS
        hard_max_rounds = engine["hard_max_rounds"]
        scenarios.append(
            {
                "index": index,
                "id": scenario_id,
                "target_characters": target,
                "tier": tier,
                "max_rounds": max_rounds,
                "min_round_gain": DEFAULT_MIN_ROUND_GAIN,
                "max_stall_retries": engine["max_stall_retries"],
                "hard_max_rounds": (
                    max_rounds if hard_max_rounds is None else hard_max_rounds
                ),
                "closing_hint": engine["closing_hint"],
                "overshoot_guard": engine["overshoot_guard"],
                "stall_strategy": engine["stall_strategy"],
            }
        )
    return scenarios


def _failure_class(record: dict) -> str:
    reason = record["stop_reason"]
    if reason == TARGET_REACHED:
        # 只有 within_tolerance 为假时才会走到这里，即超冲。
        return "overshoot"
    return reason


def run_scenario(
    scenario: dict,
    work_dir: Path,
    seed: int,
    provider_factory=None,
) -> tuple[dict, dict]:
    """跑一个场景，返回 (确定性记录, 耗时观测)。

    provider_factory 只给「提示响应敏感性」附录用；主套件始终使用
    FakeProvider（对提示无响应），因此 headline 数字不受该假设影响。
    """
    tier = scenario["tier"]
    target = scenario["target_characters"]
    rng = _scenario_rng(seed, scenario["index"])

    # 既有正文：用一个独立的随机流生成，避免影响产出序列。
    body_rng = random.Random(seed * 1000 + scenario["index"] + 500_000)
    body_length = max(200, int(round(target * 0.3)))
    body_text = _slice_text(body_length, body_rng)
    body_path = work_dir / f"body-{scenario['id']}.txt"
    body_path.write_text(body_text, encoding="utf-8")
    body_hash_before = _sha256_bytes(body_path.read_bytes())

    factory = provider_factory or FakeProvider
    provider = factory(tier, target, rng)

    budget_check = None
    should_cancel = None
    if tier == "budget_limited":
        # 第 1、2 轮放行，第 3 轮开始前报告预算耗尽。
        budget_check = lambda round_index: round_index <= 2  # noqa: E731
    elif tier == "canceled":
        # 第 1 轮正常生成，第 2 轮开始前作者取消。
        should_cancel = lambda: provider.calls >= 1  # noqa: E731

    started = time.perf_counter()
    result = run_budgeted_continuation(
        provider,
        target_characters=target,
        max_rounds=scenario["max_rounds"],
        min_round_gain=scenario["min_round_gain"],
        max_stall_retries=scenario["max_stall_retries"],
        hard_max_rounds=scenario["hard_max_rounds"],
        closing_hint=scenario["closing_hint"],
        overshoot_guard=scenario["overshoot_guard"],
        stall_strategy=scenario["stall_strategy"],
        budget_check=budget_check,
        should_cancel=should_cancel,
    )
    elapsed = time.perf_counter() - started
    overhead = max(0.0, elapsed - provider.generate_seconds)

    body_hash_after = _sha256_bytes(body_path.read_bytes())
    body_unchanged = body_hash_before == body_hash_after

    lower, upper = target_range(target)
    characters = result["characters"]
    first_pass = (
        result["stop_reason"] == TARGET_REACHED and result["within_tolerance"] is True
    )

    record = {
        "id": scenario["id"],
        "target_characters": target,
        "tier": tier,
        "tier_label": TIERS[tier]["label"],
        "max_rounds": scenario["max_rounds"],
        "min_round_gain": scenario["min_round_gain"],
        "max_stall_retries": scenario["max_stall_retries"],
        "hard_max_rounds": scenario["hard_max_rounds"],
        "stop_reason": result["stop_reason"],
        "stop_reason_label": STOP_REASON_LABELS[result["stop_reason"]],
        "rounds": result["round_count"],
        "characters": characters,
        "target_range": {"minimum": lower, "maximum": upper},
        "target_reached": bool(result["target_reached"]),
        "within_tolerance": bool(result["within_tolerance"]),
        "first_pass": bool(first_pass),
        "overshoot": max(0, characters - upper),
        "shortfall": max(0, lower - characters),
        # v1.1 新增字段；v1.0 锁定模式下恒为 0。
        "retries": result["retries"],
        "extended_rounds": result["extended_rounds"],
        "retried_rounds": [
            round_record["round_index"]
            for round_record in result["rounds"]
            if round_record.get("retried")
        ],
        "closing_hint_rounds": provider.closing_hint_rounds,
        "stall_hint_rounds": provider.stall_hint_rounds,
        "hint_rounds": {
            key: list(value) for key, value in provider.hint_round_indexes.items()
        },
        # v1.2：超冲额度提示的送达情况与「本次至少写 N 字」的额度序列。
        "overshoot_hint_rounds": provider.overshoot_hint_rounds,
        "overshoot_hint_round_indexes": list(provider.overshoot_hint_round_indexes),
        "characters_over_upper": max(0, characters - upper),
        "stall_min_characters": list(provider.stall_min_characters),
        "stall_strategies": [
            item["strategy"] for item in result["stall_hints"] if item["strategy"]
        ],
        "engine_flags": {
            "closing_hint": scenario["closing_hint"],
            "overshoot_guard": scenario["overshoot_guard"],
            "stall_strategy": scenario["stall_strategy"],
        },
        "provider": provider.provider_name,
        "obeyed_rounds": provider.obeyed_rounds,
        "body_sha256": body_hash_before,
        "body_unchanged": body_unchanged,
        "candidate_only": bool(result["candidate_only"]),
        "accounting_ok": characters == provider.emitted,
    }
    if not first_pass:
        record["failure_class"] = _failure_class(record)
        record["failure_label"] = FAILURE_CLASS_LABELS[record["failure_class"]]

    timing = {
        "id": scenario["id"],
        "total_ms": round(elapsed * 1000, 4),
        "engine_overhead_ms": round(overhead * 1000, 4),
        "fake_generate_ms": round(provider.generate_seconds * 1000, 4),
        "rounds": result["round_count"],
    }
    return record, timing


def run_suite(
    seed: int,
    work_dir: Path,
    variant: str = "v1",
    provider_factory=None,
    hard_max_rounds: int | None = None,
    max_stall_retries: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """跑整套场景。三个 override 只给附录的敏感性实验用。"""
    records: list[dict] = []
    timings: list[dict] = []
    for scenario in _build_scenarios(variant):
        if hard_max_rounds is not None:
            scenario = dict(scenario, hard_max_rounds=hard_max_rounds)
        if max_stall_retries is not None:
            scenario = dict(scenario, max_stall_retries=max_stall_retries)
        record, timing = run_scenario(
            scenario, work_dir, seed, provider_factory=provider_factory
        )
        records.append(record)
        timings.append(timing)
    return records, timings


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _group_summary(records: list[dict]) -> dict:
    total = len(records)
    passed = sum(1 for record in records if record["first_pass"])
    return {
        "total": total,
        "first_pass": passed,
        "failed": total - passed,
        "rate": _rate(passed, total),
    }


def summarize(records: list[dict]) -> dict:
    total = len(records)
    passed = [record for record in records if record["first_pass"]]

    by_target: dict[str, dict] = {}
    for target in sorted({record["target_characters"] for record in records}):
        by_target[str(target)] = _group_summary(
            [record for record in records if record["target_characters"] == target]
        )

    by_tier: dict[str, dict] = {}
    for tier in TIER_ORDER:
        rows = [record for record in records if record["tier"] == tier]
        summary = _group_summary(rows)
        summary["mean_rounds"] = (
            round(statistics.fmean([record["rounds"] for record in rows]), 4)
            if rows
            else 0.0
        )
        summary["stop_reasons"] = {
            reason: sum(1 for record in rows if record["stop_reason"] == reason)
            for reason in STOP_REASONS
        }
        by_tier[tier] = summary

    stop_reasons = {
        reason: sum(1 for record in records if record["stop_reason"] == reason)
        for reason in STOP_REASONS
    }

    rounds = [record["rounds"] for record in records]
    characters = [record["characters"] for record in records]
    overshoot_records = [record for record in records if record["overshoot"] > 0]
    shortfall_records = [record for record in records if record["shortfall"] > 0]

    failures = [
        {
            "id": record["id"],
            "target_characters": record["target_characters"],
            "tier": record["tier"],
            "tier_label": record["tier_label"],
            "stop_reason": record["stop_reason"],
            "failure_class": record["failure_class"],
            "failure_label": record["failure_label"],
            "rounds": record["rounds"],
            "characters": record["characters"],
            "shortfall": record["shortfall"],
            "overshoot": record["overshoot"],
        }
        for record in records
        if not record["first_pass"]
    ]

    def subset(tiers: tuple[str, ...]) -> dict:
        return _group_summary([record for record in records if record["tier"] in tiers])

    qualified = subset(QUALIFIED_TIERS)
    deficient = subset(DEFICIENT_TIERS)
    interrupted = subset(INTERRUPTED_TIERS)

    # 断点分析：若合格档通过率为 q、非合格档（劣质 + 中断）通过率为 d，
    # 非合格档占比 f 时总体通过率 = (1-f)·q + f·d。求使总体 ≥ 80% 的最大 f。
    break_even = None
    nonqualified = subset(DEFICIENT_TIERS + INTERRUPTED_TIERS)
    if qualified["total"] and nonqualified["total"]:
        q = qualified["rate"]
        d = nonqualified["rate"]
        if q > Q1_TARGET_RATE and q > d:
            break_even = {
                "qualified_rate": q,
                "nonqualified_rate": d,
                "max_nonqualified_fraction": round((q - Q1_TARGET_RATE) / (q - d), 4),
                "observed_nonqualified_fraction": round(
                    nonqualified["total"] / total, 4
                ),
            }

    return {
        "total": total,
        "first_pass": len(passed),
        "failed": total - len(passed),
        "rate": _rate(len(passed), total),
        "by_target": by_target,
        "by_tier": by_tier,
        "qualified": qualified,
        "deficient": deficient,
        "interrupted": interrupted,
        "stop_reasons": stop_reasons,
        "rounds": {
            "mean": round(statistics.fmean(rounds), 4),
            "median": round(statistics.median(rounds), 4),
            "minimum": min(rounds),
            "maximum": max(rounds),
            "total": sum(rounds),
        },
        "characters": {
            "mean": round(statistics.fmean(characters), 2),
            "minimum": min(characters),
            "maximum": max(characters),
            "total": sum(characters),
        },
        "overshoot": {
            "count": len(overshoot_records),
            "maximum": max((record["overshoot"] for record in records), default=0),
        },
        "shortfall": {
            "count": len(shortfall_records),
            "maximum": max((record["shortfall"] for record in records), default=0),
        },
        "failures": failures,
        "body_invariance": {
            "checked": total,
            "unchanged": sum(1 for record in records if record["body_unchanged"]),
            "all_unchanged": all(record["body_unchanged"] for record in records),
        },
        "candidate_only": {
            "checked": total,
            "all_candidate_only": all(record["candidate_only"] for record in records),
        },
        "accounting": {
            "checked": total,
            "all_consistent": all(record["accounting_ok"] for record in records),
        },
        # v1.1 新增能力的触发统计。v1.0 锁定模式下这些值恒为 0。
        "engine_features": {
            "retries": sum(record["retries"] for record in records),
            "retried_scenarios": [
                record["id"] for record in records if record["retries"] > 0
            ],
            "extended_rounds": sum(record["extended_rounds"] for record in records),
            "extended_scenarios": [
                record["id"] for record in records if record["extended_rounds"] > 0
            ],
            "closing_hint_rounds": sum(
                record["closing_hint_rounds"] for record in records
            ),
            "closing_hint_scenarios": [
                record["id"] for record in records if record["closing_hint_rounds"] > 0
            ],
            "stall_hint_rounds": sum(record["stall_hint_rounds"] for record in records),
            "stall_hint_scenarios": [
                record["id"] for record in records if record["stall_hint_rounds"] > 0
            ],
            # v1.2：超冲额度提示的覆盖面，以及停滞换策略实际给出的最少字数。
            "overshoot_hint_rounds": sum(
                record["overshoot_hint_rounds"] for record in records
            ),
            "overshoot_hint_scenarios": [
                record["id"]
                for record in records
                if record["overshoot_hint_rounds"] > 0
            ],
            "stall_min_characters": [
                value
                for record in records
                for value in record["stall_min_characters"]
            ],
            "stall_strategies": [
                value for record in records for value in record["stall_strategies"]
            ],
        },
        # 附录 B 的数据源：引擎**能控制**的与**只能观测**的分开列。
        "engine_control": {
            "controlled": {
                "rounds_total": sum(rounds),
                "stop_reason_decided": total,
                "lower_bound_hits": sum(
                    1
                    for record in records
                    if record["target_reached"]
                    and record["characters"] >= record["target_range"]["minimum"]
                ),
                "envelope_rounds": sum(
                    record["overshoot_hint_rounds"] for record in records
                ),
                "stall_retry_rounds": sum(record["retries"] for record in records),
            },
            "observed_only": {
                "overshoot_characters": sum(record["overshoot"] for record in records),
                "overshoot_scenarios": [
                    record["id"] for record in records if record["overshoot"] > 0
                ],
                "stall_scenarios": [
                    record["id"] for record in records if record["retries"] > 0
                ],
                "strategy_switches": sum(
                    len(record["stall_strategies"]) for record in records
                ),
                "obedience": "对抗式 provider 不读任何提示；模型服从度只能由附录估计",
            },
        },
        "break_even": break_even,
    }


def summarize_timing(timings: list[dict]) -> dict:
    total_ms = [item["total_ms"] for item in timings]
    overhead_ms = [item["engine_overhead_ms"] for item in timings]
    generate_ms = [item["fake_generate_ms"] for item in timings]
    return {
        "note": (
            "墙钟耗时属于观测值，不参与确定性比对。engine_overhead_ms 已扣除 "
            "fake provider 自身的文本生成时间，是引擎循环与记账的净开销；"
            "两者都不含真实模型推理延迟。"
        ),
        "scenarios": len(timings),
        "total_ms": {
            "mean": round(statistics.fmean(total_ms), 4),
            "median": round(statistics.median(total_ms), 4),
            "minimum": round(min(total_ms), 4),
            "maximum": round(max(total_ms), 4),
            "sum": round(sum(total_ms), 4),
        },
        "engine_overhead_ms": {
            "mean": round(statistics.fmean(overhead_ms), 4),
            "median": round(statistics.median(overhead_ms), 4),
            "minimum": round(min(overhead_ms), 4),
            "maximum": round(max(overhead_ms), 4),
            "sum": round(sum(overhead_ms), 4),
        },
        "fake_generate_ms": {
            "mean": round(statistics.fmean(generate_ms), 4),
            "sum": round(sum(generate_ms), 4),
        },
        "per_scenario": timings,
    }


# --------------------------------------------------------------------------- #
# 敏感性附录（非计入）：换 provider 假设 / 换重试次数会怎样
# --------------------------------------------------------------------------- #


def _sensitivity_agg(records: list[dict]) -> dict:
    summary = summarize(records)
    return {
        "first_pass": summary["first_pass"],
        "total": summary["total"],
        "rate": summary["rate"],
        "target_3000": dict(summary["by_target"][str(Q1_TARGET_CHARACTERS)]),
        "rounds_total": summary["rounds"]["total"],
        "rounds_mean": summary["rounds"]["mean"],
        "obeyed_rounds": sum(record["obeyed_rounds"] for record in records),
        "stall_min_characters": list(
            summary["engine_features"]["stall_min_characters"]
        ),
        "failures": [row["id"] for row in summary["failures"]],
    }


def _pick(records: list[dict], scenario_id: str) -> dict | None:
    for record in records:
        if record["id"] == scenario_id:
            return record
    return None


def _focus_row(record: dict, label: str, note: str, counted: bool = False) -> dict:
    return {
        "label": label,
        "counted": counted,
        "characters": record["characters"],
        "rounds": record["rounds"],
        "stop_reason": record["stop_reason"],
        "within_tolerance": record["within_tolerance"],
        "first_pass": record["first_pass"],
        "overshoot": record["overshoot"],
        "shortfall": record["shortfall"],
        "note": note,
    }


def sensitivity_appendix(
    seed: int, work_dir: Path, baseline_report: dict
) -> dict:
    """附录 A：换成「服从提示」的 provider，同一套 20 场景会得到什么。

    **不计入** first_pass / headline 数字：判定口径一个字都没改，这里只是把
    「指标依赖模型是否服从长度约束」这个前提摊开，给出上界与成因。

    三组对照：

    - ``obey_engine_hints``：只服从引擎自己注入的提示（首轮无额度可服从）；
    - ``obey_with_first_round_envelope``：额外模拟调用方用 ``round_budget()``
      把额度合进开场提示；
    - ``obey_long_rounds``：同上，但把 ``hard_max_rounds`` 放到引擎允许的 24。
    """
    appendix_dir = work_dir / "sensitivity"
    appendix_dir.mkdir(parents=True, exist_ok=True)

    def plain(tier, target, rng):
        return ObeyingProvider(tier, target, rng)

    def envelope(tier, target, rng):
        return ObeyingProvider(tier, target, rng, obey_first_round_envelope=True)

    obey_records, _ = run_suite(seed, appendix_dir, "v1.2", provider_factory=plain)
    envelope_records, _ = run_suite(
        seed, appendix_dir, "v1.2", provider_factory=envelope
    )
    long_records, _ = run_suite(
        seed, appendix_dir, "v1.2", provider_factory=plain, hard_max_rounds=24
    )
    baseline_records = baseline_report["scenarios"]
    long_envelope_records, _ = run_suite(
        seed,
        appendix_dir,
        "v1.2",
        provider_factory=envelope,
        hard_max_rounds=24,
    )

    def pick(records, scenario_id, label, note, counted=False):
        record = _pick(records, scenario_id)
        return _focus_row(record, label, note, counted) if record else None

    focus = {
        "sc-15": [
            pick(baseline_records, "sc-15", "对抗式 provider（基线）",
                 "不读任何提示，首轮直接写 4661 字，越过 4400 上限", counted=True),
            pick(obey_records, "sc-15", "服从式（只有引擎提示）",
                 "首轮上下文按契约是空字典，没有额度可服从 → 仍然超冲"),
            pick(envelope_records, "sc-15", "服从式 + 调用方合入首轮额度",
                 "round_budget(0, 4000) 给出 4400 字上限，首轮落在界内"),
        ],
        "sc-18": [
            pick(baseline_records, "sc-18", "对抗式 provider（基线）",
                 "每轮 12–30 字且不读提示，12 轮上限也只有约 250 字", counted=True),
            pick(obey_records, "sc-18", "服从式（引擎提示，12 轮）",
                 "每次停滞重试至少写 stall_retry_min_characters 字"),
            pick(long_records, "sc-18", "服从式（引擎提示，24 轮）",
                 "hard_max_rounds 放到引擎允许上限 24 才够摊到下限"),
            pick(long_envelope_records, "sc-18", "服从式（24 轮 + 首轮额度）",
                 "首轮额度与停滞额度同时生效"),
        ],
    }

    return {
        "non_counting": True,
        "note": (
            "本节全部数字均为**非计入**的敏感性上界：first_pass 口径未改动，"
            "headline 只认对抗式 provider。上界高不代表达标，只代表「模型服从时能达标」。"
        ),
        "baseline": _sensitivity_agg(baseline_records),
        "obey_engine_hints": _sensitivity_agg(obey_records),
        "obey_with_first_round_envelope": _sensitivity_agg(envelope_records),
        "obey_long_rounds": _sensitivity_agg(long_records),
        "focus": focus,
    }


def retry_sensitivity(seed: int, work_dir: Path) -> dict:
    """附录 C：对抗式 provider 下 ``max_stall_retries`` 1 次 vs 2 次的真实成本。

    每次重试在生产里就是一次额外的模型调用，所以「多试一次没坏处」不成立。
    """
    retry_dir = work_dir / "retry-sensitivity"
    retry_dir.mkdir(parents=True, exist_ok=True)
    one_records, _ = run_suite(seed, retry_dir, "v1.2", max_stall_retries=1)
    two_records, _ = run_suite(seed, retry_dir, "v1.2", max_stall_retries=2)
    one = _sensitivity_agg(one_records)
    two = _sensitivity_agg(two_records)

    flips: list[dict] = []
    for index, record in enumerate(one_records):
        other = two_records[index]
        if record["first_pass"] != other["first_pass"]:
            flips.append(
                {
                    "id": record["id"],
                    "from": record["stop_reason"],
                    "to": other["stop_reason"],
                }
            )

    stall_ids = [record["id"] for record in one_records if record["retries"] > 0]
    two_stall_ids = [record["id"] for record in two_records if record["retries"] > 0]
    return {
        "non_counting": True,
        "retries_1": one,
        "retries_2": two,
        "delta_rounds": two["rounds_total"] - one["rounds_total"],
        "delta_first_pass": two["first_pass"] - one["first_pass"],
        "flips": flips,
        "stall_scenarios_retries_1": stall_ids,
        "stall_scenarios_retries_2": two_stall_ids,
        "note": (
            "对抗式 provider 不读提示：多一次重试只会多一次模型调用，"
            "轮数/字符数增加而通过数不变，因此默认值保持 1。"
        ),
    }


# --------------------------------------------------------------------------- #
# 确定性
# --------------------------------------------------------------------------- #


def _canonical_payload(report: dict) -> dict:
    """参与确定性比对的部分：不含墙钟时间、不含环境快照。"""
    return {
        "seed": report["config"]["seed"],
        "config": report["config"],
        "scenarios": report["scenarios"],
        "summary": report["summary"],
    }


def digest_of(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_summary(report: dict, paths: dict[str, str] | None = None) -> str:
    """results/budgeted-baseline.md：一页纸摘要。"""
    paths = paths or _paths_for((report.get("engine") or {}).get("variant", "v1"))
    summary = report["summary"]
    timing = report["timing"]
    lines: list[str] = []
    lines.append("# 多轮字数预算续写 · 一次通过率基准（摘要）")
    lines.append("")
    lines.append(
        f"> 由 `benchmarks/budgeted/run.py` 自动生成，seed={report['config']['seed']}，"
        f"与 `{paths['json']}` 同源。"
    )
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(
        _table(
            ["指标", "数值"],
            [
                ["场景数", str(summary["total"])],
                [
                    f"一次通过率（全部场景）",
                    f"{_pct(summary['rate'])}（{summary['first_pass']}/{summary['total']}）",
                ],
                [
                    "一次通过率（3000 字子集，Q1 口径）",
                    f"{_pct(summary['by_target'][str(Q1_TARGET_CHARACTERS)]['rate'])}"
                    f"（{summary['by_target'][str(Q1_TARGET_CHARACTERS)]['first_pass']}"
                    f"/{summary['by_target'][str(Q1_TARGET_CHARACTERS)]['total']}）",
                ],
                [
                    "一次通过率（生成侧合格档）",
                    f"{_pct(summary['qualified']['rate'])}"
                    f"（{summary['qualified']['first_pass']}/{summary['qualified']['total']}）",
                ],
                ["平均轮数", f"{summary['rounds']['mean']:.2f}"],
                [
                    "引擎净耗时（观测值）",
                    f"见 {paths['timing']}",
                ],
                [
                    "正文被改写",
                    f"{summary['body_invariance']['checked'] - summary['body_invariance']['unchanged']}"
                    f" / {summary['body_invariance']['checked']}",
                ],
            ],
        )
    )
    lines.append("")
    lines.append("## 按目标字数")
    lines.append("")
    target_rows = []
    for target in sorted(summary["by_target"], key=int):
        row = summary["by_target"][target]
        target_rows.append(
            [
                target,
                str(row["total"]),
                f"{row['first_pass']}/{row['total']}",
                _pct(row["rate"]),
            ]
        )
    lines.append(_table(["目标字数", "场景", "一次通过", "通过率"], target_rows))
    lines.append("")
    lines.append("## 按质量档位")
    lines.append("")
    rows = []
    for tier in TIER_ORDER:
        row = summary["by_tier"][tier]
        if row["total"] == 0:
            continue
        rows.append(
            [
                TIERS[tier]["label"],
                tier,
                str(row["total"]),
                f"{row['first_pass']}/{row['total']}",
                _pct(row["rate"]),
                f"{row['mean_rounds']:.2f}",
            ]
        )
    lines.append(_table(["档位", "标识", "场景", "通过", "通过率", "平均轮数"], rows))
    lines.append("")
    lines.append("## 停止原因分布")
    lines.append("")
    lines.append(
        _table(
            ["停止原因", "含义", "场景数"],
            [
                [reason, STOP_REASON_LABELS[reason], str(summary["stop_reasons"][reason])]
                for reason in STOP_REASONS
            ],
        )
    )
    lines.append("")
    lines.append("## 确定性")
    lines.append("")
    lines.append(
        f"- 重复运行：{report['determinism']['repeat']} 次，结果一致："
        f"{'是' if report['determinism']['identical'] else '否'}"
    )
    lines.append(f"- 确定性摘要：`{report['determinism']['digest']}`")
    lines.append("")
    lines.append(f"完整方法、参数、失败分类与局限见 `{paths['doc']}`。")
    lines.append("")
    return "\n".join(lines)


def render_doc(report: dict, paths: dict[str, str] | None = None) -> str:
    """docs/consistency-budget-baseline-2026-10-23.md：正式基线文档。"""
    paths = paths or _paths_for((report.get("engine") or {}).get("variant", "v1"))
    config = report["config"]
    summary = report["summary"]
    timing = report["timing"]
    determinism = report["determinism"]
    environment = report["environment"]
    q1 = summary["by_target"][str(Q1_TARGET_CHARACTERS)]

    lines: list[str] = []
    lines.append("# 墨屿多轮字数预算续写 · 一次通过率基线（2026-10-23）")
    lines.append("")
    lines.append(
        "> 任务 t10 / Q1-5b。把策略文档 Q1 的验收指标「3000 字章，一次通过率 ≥80%」"
        "变成可复现的数字。本文件由 `benchmarks/budgeted/run.py` 自动生成，"
        f"所有数字与 `{paths['json']}` 同源，"
        "请勿手工修改。"
    )
    lines.append("")

    # ---------------- 摘要 ----------------
    lines.append("## 1. 摘要")
    lines.append("")
    lines.append(
        _table(
            ["指标", "数值", "说明"],
            [
                ["场景数", str(summary["total"]), "20 个章节场景，覆盖 2500/3000/4000 字"],
                [
                    "一次通过率（全部场景）",
                    f"{_pct(summary['rate'])}（{summary['first_pass']}/{summary['total']}）",
                    "含劣质生成与外部中断场景，见配比假设",
                ],
                [
                    "一次通过率（3000 字子集）",
                    f"{_pct(q1['rate'])}（{q1['first_pass']}/{q1['total']}）",
                    "Q1 指标的直接对应口径",
                ],
                [
                    "一次通过率（生成侧合格档）",
                    f"{_pct(summary['qualified']['rate'])}"
                    f"（{summary['qualified']['first_pass']}/{summary['qualified']['total']}）",
                    "calibrated / steady / cautious",
                ],
                [
                    "一次通过率（生成侧劣质档）",
                    f"{_pct(summary['deficient']['rate'])}"
                    f"（{summary['deficient']['first_pass']}/{summary['deficient']['total']}）",
                    "volatile / underpowered / stalling",
                ],
                [
                    "一次通过率（外部中断档）",
                    f"{_pct(summary['interrupted']['rate'])}"
                    f"（{summary['interrupted']['first_pass']}/{summary['interrupted']['total']}）",
                    "budget_limited / canceled",
                ],
                ["平均轮数", f"{summary['rounds']['mean']:.2f}", f"中位 {summary['rounds']['median']:.2f}，范围 {summary['rounds']['minimum']}–{summary['rounds']['maximum']}"],
                ["引擎净耗时（观测值）", "见 `results/budgeted-timing.json`", "墙钟观测值，不进入确定性报告；不含真实模型延迟"],
                ["正文被改写次数", "0", f"{summary['body_invariance']['checked']} 个场景运行前后 sha256 全部一致"],
                ["确定性摘要", f"`{determinism['digest']}`", f"同 seed 重复 {determinism['repeat']} 次逐字节一致"],
            ],
        )
    )
    lines.append("")

    if report["q1_target_met"]:
        lines.append(
            f"**Q1 目标：达成。** 3000 字子集一次通过率 {_pct(q1['rate'])}，"
            f"不低于 {_pct(Q1_TARGET_RATE)}。"
        )
    else:
        lines.append(
            f"**Q1 目标：未达成。** 3000 字子集一次通过率 {_pct(q1['rate'])}，"
            f"低于 {_pct(Q1_TARGET_RATE)}。差距来源见 §5.5 与 §6，"
            f"全部来自生成侧劣质档与外部中断，不是引擎自身的判定缺陷。"
        )
    lines.append("")

    # ---------------- 定义 ----------------
    lines.append("## 2. 一次通过的定义")
    lines.append("")
    lines.append(
        "一个场景记为「一次通过」，当且仅当同时满足：\n"
        "\n"
        "1. `stop_reason == \"target_reached\"`；\n"
        "2. `within_tolerance` 为真，即累计字数落在 "
        "`[ceil(0.9×目标), floor(1.1×目标)]` 内。\n"
        "\n"
        "引擎在累计字数达到下限（0.9 倍目标）时立即停止，因此**单轮产出过大、"
        "越过 1.1 倍上限时 `within_tolerance` 为假，本基准判为失败**，"
        "不放宽成只看 `target_reached`。这一条决定了 `volatile` 档的判定结果。"
    )
    lines.append("")

    # ---------------- 方法 ----------------
    lines.append("## 3. 方法")
    lines.append("")
    lines.append("### 3.1 被测引擎")
    lines.append("")
    lines.append(
        f"- 模块：`backend/moyu_continuation.py`，sha256 `{environment['engine_sha256'][:16]}…`\n"
        f"- 入口：`run_budgeted_continuation(generate, *, target_characters, max_rounds, "
        f"min_round_gain, budget_check, should_cancel, context_builder)`\n"
        f"- 字数口径：`{config['counting_rule']}`（非空白 Unicode 字符，标点计入、空白不计）\n"
        f"- 容差：±{int(config['tolerance'] * 100)}%，下限向上取整、上限向下取整"
    )
    lines.append("")
    lines.append("### 3.2 fake provider 模型")
    lines.append("")
    lines.append(
        "基准不调用任何真实模型。每个场景注入一个内存 fake provider，"
        "它按「剩余额度 × 档位比例」决定本轮产出长度，比例带固定区间的均匀抖动，"
        "随机流由 `seed` 派生，因此完全可复现。产出的正文取自内置中文语料，"
        "长度精确、不含空白字符，保证 `count_characters` 与产出长度一致。"
    )
    lines.append("")
    lines.append(
        _table(
            ["档位", "标识", "单轮产出模型", "预期行为"],
            [
                [
                    TIERS[tier]["label"],
                    tier,
                    TIERS[tier]["yield_note"],
                    TIERS[tier]["behaviour"],
                ]
                for tier in TIER_ORDER
            ],
        )
    )
    lines.append("")
    lines.append("### 3.3 场景矩阵")
    lines.append("")
    lines.append(
        "20 个场景由「目标字数 × 质量档位」交叉构成，目标字数取 2500 / 3000 / 4000，"
        "`max_rounds` 统一取引擎默认值 6，`min_round_gain` 取默认值 50。"
    )
    lines.append("")
    lines.append(
        _table(
            ["场景", "目标字数", "质量档位", "max_rounds", "min_round_gain"],
            [
                [
                    record["id"],
                    str(record["target_characters"]),
                    f"{record['tier_label']}（{record['tier']}）",
                    str(record["max_rounds"]),
                    str(record["min_round_gain"]),
                ]
                for record in report["scenarios"]
            ],
        )
    )
    lines.append("")
    lines.append("### 3.4 配比假设（重要）")
    lines.append("")
    lines.append(
        "20 个场景的档位配比是**假设**，不是从任何真实模型测出来的分布。"
        "它模拟一条已接入生产、但生成质量参差的链路："
        f"生成侧合格档 {summary['qualified']['total']} 个"
        f"（{summary['qualified']['total'] / summary['total']:.0%}），"
        f"生成侧劣质档 {summary['deficient']['total']} 个"
        f"（{summary['deficient']['total'] / summary['total']:.0%}），"
        f"外部中断档 {summary['interrupted']['total']} 个"
        f"（{summary['interrupted']['total'] / summary['total']:.0%}）。"
        "因此**总体一次通过率是配比的函数**，换一套配比就会得到另一个数字。"
        "为避免单点数字误导，§5.2 给出每个档位的收敛包络，§6 给出断点条件，"
        "读者可以按自己的质量分布重新加权。"
    )
    lines.append("")
    lines.append("### 3.5 确定性口径")
    lines.append("")
    lines.append(
        "确定性核心 = `seed` + `config` + `scenarios` + `summary`，不含任何墙钟时间。"
        f"同一命令重复 {determinism['repeat']} 次，核心逐字节一致，"
        f"digest 为 `{determinism['digest']}`。墙钟耗时属于观测值，"
        "只写入 `results/budgeted-timing.json`，**不进入 JSON / Markdown / 本文档 / stdout**，"
        "因此同一 seed 的两次运行会产出逐字节相同的全部确定性产物。"
    )
    lines.append("")
    lines.append("### 3.6 正文不变性口径")
    lines.append("")
    lines.append(
        "每个场景在运行前把一段既有正文写入独立文件，记录 sha256；"
        "运行结束后重新读取并比对。引擎的返回值只有 `candidate_only = true` 的候选文本，"
        "任何对正文的写入都会让哈希变化并被本基准捕获。"
    )
    lines.append("")

    # ---------------- 参数 ----------------
    lines.append("## 4. 参数与环境")
    lines.append("")
    lines.append(
        _table(
            ["项", "值"],
            [
                ["seed", str(config["seed"])],
                ["场景数", str(config["scenarios"])],
                ["目标字数", "、".join(str(value) for value in config["targets"])],
                ["max_rounds", str(config["max_rounds"])],
                ["min_round_gain", str(config["min_round_gain"])],
                ["容差", f"±{int(config['tolerance'] * 100)}%"],
                ["字数口径", config["counting_rule"]],
                ["Q1 目标字数", str(config["q1_target_characters"])],
                ["Q1 目标通过率", _pct(config["q1_target_rate"])],
                ["Python", environment["python"]],
                ["平台", environment["platform"]],
                ["SQLite", "不适用（本基准不访问数据库）"],
                ["真实模型调用", "无（全部为内存 fake provider）"],
            ],
        )
    )
    lines.append("")

    # ---------------- 结果 ----------------
    lines.append("## 5. 结果")
    lines.append("")
    lines.append("### 5.1 按目标字数")
    lines.append("")
    target_rows = []
    for target in sorted(summary["by_target"], key=int):
        row = summary["by_target"][target]
        target_rows.append(
            [
                target,
                str(row["total"]),
                f"{row['first_pass']}/{row['total']}",
                _pct(row["rate"]),
            ]
        )
    lines.append(_table(["目标字数", "场景", "一次通过", "通过率"], target_rows))
    lines.append("")
    lines.append("### 5.2 按质量档位（收敛包络）")
    lines.append("")
    tier_rows = []
    for tier in TIER_ORDER:
        row = summary["by_tier"][tier]
        if row["total"] == 0:
            continue
        tier_rows.append(
            [
                TIERS[tier]["label"],
                tier,
                str(row["total"]),
                f"{row['first_pass']}/{row['total']}",
                _pct(row["rate"]),
                f"{row['mean_rounds']:.2f}",
                str(row["stop_reasons"][TARGET_REACHED]),
                str(row["stop_reasons"][NO_PROGRESS]),
                str(row["stop_reasons"][MAX_ROUNDS]),
                str(row["stop_reasons"][BUDGET_EXHAUSTED]),
                str(row["stop_reasons"][CANCELED]),
                str(row["stop_reasons"][OVERSHOOT]),
            ]
        )
    lines.append(
        _table(
            [
                "档位",
                "标识",
                "场景",
                "通过",
                "通过率",
                "平均轮数",
                "达标",
                "停滞",
                "轮尽",
                "预算",
                "取消",
                "超冲",
            ],
            tier_rows,
        )
    )
    lines.append("")
    lines.append("### 5.3 停止原因分布")
    lines.append("")
    lines.append(
        _table(
            ["停止原因", "含义", "场景数", "占比"],
            [
                [
                    reason,
                    STOP_REASON_LABELS[reason],
                    str(summary["stop_reasons"][reason]),
                    _pct(summary["stop_reasons"][reason] / summary["total"]),
                ]
                for reason in STOP_REASONS
            ],
        )
    )
    lines.append("")
    lines.append("### 5.4 轮数与字数")
    lines.append("")
    lines.append(
        _table(
            ["指标", "值"],
            [
                ["总轮数", str(summary["rounds"]["total"])],
                ["平均轮数", f"{summary['rounds']['mean']:.2f}"],
                ["中位轮数", f"{summary['rounds']['median']:.2f}"],
                ["最少 / 最多轮数", f"{summary['rounds']['minimum']} / {summary['rounds']['maximum']}"],
                ["生成总字数", str(summary["characters"]["total"])],
                ["平均字数", f"{summary['characters']['mean']:.1f}"],
                ["超冲场景数", str(summary["overshoot"]["count"])],
                ["最大超冲字数", str(summary["overshoot"]["maximum"])],
                ["欠量场景数", str(summary["shortfall"]["count"])],
                ["最大欠量字数", str(summary["shortfall"]["maximum"])],
            ],
        )
    )
    lines.append("")
    lines.append("### 5.5 失败场景分类")
    lines.append("")
    if summary["failures"]:
        lines.append(
            _table(
                ["场景", "目标", "档位", "停止原因", "失败分类", "轮数", "字数", "欠量", "超冲"],
                [
                    [
                        item["id"],
                        str(item["target_characters"]),
                        f"{item['tier_label']}（{item['tier']}）",
                        item["stop_reason"],
                        item["failure_label"],
                        str(item["rounds"]),
                        str(item["characters"]),
                        str(item["shortfall"]),
                        str(item["overshoot"]),
                    ]
                    for item in summary["failures"]
                ],
            )
        )
        lines.append("")
        lines.append("失败原因聚合：")
        lines.append("")
        aggregate: dict[str, int] = {}
        for item in summary["failures"]:
            aggregate[item["failure_label"]] = aggregate.get(item["failure_label"], 0) + 1
        lines.append(
            _table(
                ["失败分类", "场景数"],
                [[label, str(count)] for label, count in aggregate.items()],
            )
        )
        lines.append("")
        lines.append("按目标字数归因：")
        lines.append("")
        attribution_rows = []
        for target in sorted(summary["by_target"], key=int):
            rows = [
                item
                for item in summary["failures"]
                if item["target_characters"] == int(target)
            ]
            detail = "、".join(
                f"{item['id']}（{item['tier']}，{item['failure_class']}）" for item in rows
            )
            attribution_rows.append(
                [target, f"{len(rows)}/{summary['by_target'][target]['total']}", detail or "无"]
            )
        lines.append(_table(["目标字数", "失败/场景", "失败场景与归因"], attribution_rows))
    else:
        lines.append("无失败场景。")
    lines.append("")

    lines.append("### 5.6 耗时")
    lines.append("")
    lines.append(
        "墙钟耗时是**观测值**：同一 seed 的两次运行必然有微秒级抖动，"
        "因此它不进入 JSON / Markdown / 本文档，单独记录在 "
        "`benchmarks/budgeted/results/budgeted-timing.json`，以免破坏可复现性。"
    )
    lines.append("")
    lines.append(
        _table(
            ["确定性工作量指标", "值"],
            [
                ["场景数", str(timing["scenarios"])],
                ["引擎处理的总轮数", str(summary["rounds"]["total"])],
                ["生成的总字数", str(summary["characters"]["total"])],
                ["每场景平均轮数", f"{summary['rounds']['mean']:.2f}"],
            ],
        )
    )
    lines.append("")
    lines.append(
        "引擎净开销只含循环与记账的纯 Python 开销，**不含真实模型推理延迟**——"
        "生产环境的端到端耗时由模型调用主导，timing 文件里的数字不能当作端到端延迟使用。"
    )
    lines.append("")
    lines.append("### 5.7 正文不变性")
    lines.append("")
    lines.append(
        _table(
            ["检查项", "结果"],
            [
                [
                    "运行前后正文 sha256 一致",
                    f"{summary['body_invariance']['unchanged']}/{summary['body_invariance']['checked']}",
                ],
                ["正文被改写", "0" if summary["body_invariance"]["all_unchanged"] else "有"],
                [
                    "返回值均为 candidate_only",
                    "是" if summary["candidate_only"]["all_candidate_only"] else "否",
                ],
                [
                    "字数记账自洽（引擎计数 == provider 产出）",
                    "是" if summary["accounting"]["all_consistent"] else "否",
                ],
            ],
        )
    )
    lines.append("")

    # ---------------- 结论 ----------------
    lines.append("## 6. 结论：Q1 目标在什么条件下成立")
    lines.append("")
    qualified = summary["qualified"]
    deficient = summary["deficient"]
    interrupted = summary["interrupted"]
    lines.append(
        f"1. **引擎在生成侧合格时 100% 一次通过。** calibrated / steady / cautious "
        f"三档合计 {qualified['total']} 个场景，一次通过 "
        f"{qualified['first_pass']}/{qualified['total']}（{_pct(qualified['rate'])}），"
        f"其中保守档平均 {summary['by_tier']['cautious']['mean_rounds']:.2f} 轮，"
        "说明默认 `max_rounds=6` 足以覆盖单轮产出率低至约 38% 的模型。"
    )
    lines.append(
        f"2. **缺口全部来自生成侧与外部中断。** 劣质档 {deficient['total']} 个场景通过 "
        f"{deficient['first_pass']} 个（{_pct(deficient['rate'])}），"
        f"外部中断档 {interrupted['total']} 个场景通过 {interrupted['first_pass']} 个"
        f"（{_pct(interrupted['rate'])}）。在本基准给定的输入模型下，"
        "每个未通过场景都能归因到产出不足、超冲、停滞、预算或取消，"
        "没有出现引擎判定与输入事实不符的情况。"
    )
    break_even = summary["break_even"]
    if break_even is not None:
        lines.append(
            f"3. **断点条件。** 若合格档通过率 {_pct(break_even['qualified_rate'])}、"
            f"劣质+中断档通过率 {_pct(break_even['nonqualified_rate'])}，"
            f"则劣质+中断场景占比不超过 {_pct(break_even['max_nonqualified_fraction'])} 时，"
            f"总体一次通过率仍可保持在 {_pct(Q1_TARGET_RATE)} 以上；"
            f"本次配比的实际占比为 {_pct(break_even['observed_nonqualified_fraction'])}。"
        )
    else:
        lines.append(
            "3. **断点条件。** 合格档通过率不足以支撑 80% 目标，或劣质档通过率不低于合格档，"
            "无法给出有意义的断点。"
        )
    lines.append(
        f"4. **Q1 口径判定：{'达成' if report['q1_target_met'] else '未达成'}。** "
        f"3000 字子集 {q1['first_pass']}/{q1['total']}（{_pct(q1['rate'])}）。"
        "该子集同样包含劣质与中断场景，因此它衡量的是「引擎 + 生成质量」的系统表现，"
        "而不是引擎单独的收敛能力；引擎单独的收敛能力见第 1 条。"
    )
    lines.append(
        "5. **可交付结论。** 把 Q1 的 ≥80% 写成验收指标时，必须同时写明生成侧质量前提"
        "（例如「单轮产出率不低于剩余额度的 38%、无停滞与超冲」），否则该指标不可判定——"
        "它随生成质量分布漂移，而不是随引擎版本漂移。"
    )
    lines.append("")

    # ---------------- 局限 ----------------
    lines.append("## 7. 已知局限")
    lines.append("")
    lines.append(
        "1. **这是仿真基准，不是真实模型测量。** 全部产出由内存 fake provider 生成，"
        "没有任何真实 LLM 调用；数字反映的是引擎在给定产出模型下的行为，"
        "不能当作「某模型 3000 字章一次通过率」对外引用。"
    )
    lines.append(
        "2. **总体通过率是配比的函数。** 20 个场景的档位配比是人为假设（见 §3.4），"
        "换一套配比就会得到另一个总体数字。分层包络（§5.2）比总体数字更可信。"
    )
    lines.append(
        "3. **fake provider 过于规整。** 真实模型的单轮产出长度有长尾、会截断、"
        "会跑题、会重复；本基准只用「剩余额度 × 比例 + 均匀抖动」建模，"
        "低估了真实分布的方差。"
    )
    lines.append(
        "4. **没有建模语义质量。** 基准只测字数收敛，不判断内容是否连贯、是否重复、"
        "是否偏离大纲；一个字数达标但内容崩坏的结果在这里仍算通过。"
    )
    lines.append(
        "5. **耗时不含模型延迟。** 引擎净开销是微秒级纯 Python 开销，"
        "与生产端到端延迟无关，不能用于容量规划。"
    )
    lines.append(
        "6. **未覆盖 context_builder 的上下文增长成本。** 基准使用默认上下文构造器，"
        "真实接线若把全文累积进 prompt，token 成本会随轮数增长，本基准不测量该成本。"
    )
    lines.append(
        "7. **max_rounds 固定为 6。** 没有扫描 1–12 的轮数上限；"
        "若生产环境放宽上限，cautious 档会更早通过，underpowered 档仍然失败。"
    )
    lines.append(
        "8. **未覆盖取消/预算的时序组合。** 预算与取消各自只有一个固定触发点"
        "（第 3 轮前 / 第 2 轮前），没有测试二者同时发生或发生在其他轮次的情况。"
    )
    lines.append(
        "9. **单机单次测量。** 耗时来自一次运行内的 20 个场景，"
        "没有跨机器、跨负载的重复测量，不具备统计显著性。"
    )
    lines.append("")

    # ---------------- 复现 ----------------
    lines.append("## 8. 复现")
    lines.append("")
    lines.append("```bash")
    lines.append(".venv/bin/python benchmarks/budgeted/run.py --seed 20261023")
    lines.append("```")
    lines.append("")
    lines.append(
        "命令会重新生成 JSON / Markdown / 本文件，并在 stdout 打印每个场景的判定与"
        f"确定性摘要 `{determinism['digest']}`。退出码 0 表示**基准完整性验收**通过"
        "（20 个场景全部执行、重复运行一致、正文未被改写、6 类停止原因全部出现、"
        "报告数字自洽），**不表示一次通过率达标**；指标是否达标见 §1 与 §6。"
    )
    lines.append("")
    lines.append("产物：")
    lines.append("")
    lines.append("- `benchmarks/budgeted/results/budgeted-baseline.json`：确定性核心报告")
    lines.append("- `benchmarks/budgeted/results/budgeted-baseline.md`：一页纸摘要")
    lines.append("- `benchmarks/budgeted/results/budgeted-timing.json`：耗时观测值")
    lines.append("- `docs/consistency-budget-baseline-2026-10-23.md`：本文件")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 验收
# --------------------------------------------------------------------------- #


def _acceptance_item(item_id: str, label: str, passed: bool, detail: str) -> dict:
    return {"id": item_id, "label": label, "passed": bool(passed), "detail": detail}


def expected_doc_tokens(report: dict) -> list[tuple[str, str]]:
    """文档里必须出现的数字串，用来证明「文档数字与 JSON 一致」。"""
    summary = report["summary"]
    q1 = summary["by_target"][str(Q1_TARGET_CHARACTERS)]
    tokens: list[tuple[str, str]] = [
        ("全部场景通过数", f"{summary['first_pass']}/{summary['total']}"),
        ("全部场景通过率", _pct(summary["rate"])),
        ("3000 子集通过数", f"{q1['first_pass']}/{q1['total']}"),
        ("3000 子集通过率", _pct(q1["rate"])),
        ("合格档通过数", f"{summary['qualified']['first_pass']}/{summary['qualified']['total']}"),
        ("确定性摘要", report["determinism"]["digest"]),
        ("seed", str(report["config"]["seed"])),
        ("平均轮数", f"{summary['rounds']['mean']:.2f}"),
    ]
    for target, row in summary["by_target"].items():
        tokens.append((f"目标 {target} 通过数", f"{row['first_pass']}/{row['total']}"))
    for tier in TIER_ORDER:
        row = summary["by_tier"][tier]
        if row["total"] == 0:
            continue
        tokens.append((f"档位 {tier} 通过数", f"{row['first_pass']}/{row['total']}"))
        tokens.append((f"档位 {tier} 通过率", _pct(row["rate"])))
    for reason in STOP_REASONS:
        tokens.append((f"停止原因 {reason} 计数", str(summary["stop_reasons"][reason])))
    return tokens


def evaluate_acceptance(
    report: dict, doc_text: str, md_text: str, timing_payload: dict
) -> list[dict]:
    summary = report["summary"]
    observed_reasons = [reason for reason in STOP_REASONS if summary["stop_reasons"][reason] > 0]
    missing = [
        f"{label}={token}"
        for label, token in expected_doc_tokens(report)
        if token not in doc_text
    ]
    missing_md = [
        f"{label}={token}"
        for label, token in expected_doc_tokens(report)
        if token not in md_text
    ]
    return [
        _acceptance_item(
            "scenarios",
            "20 个场景全部执行",
            summary["total"] == len(SCENARIO_TABLE),
            f"{summary['total']}/{len(SCENARIO_TABLE)}",
        ),
        _acceptance_item(
            "determinism",
            "同 seed 重复运行结果一致",
            report["determinism"]["identical"],
            f"repeat={report['determinism']['repeat']}，digest={report['determinism']['digest']}",
        ),
        _acceptance_item(
            "body",
            "引擎未改写任何正文",
            summary["body_invariance"]["all_unchanged"],
            f"{summary['body_invariance']['unchanged']}/{summary['body_invariance']['checked']} 哈希一致",
        ),
        _acceptance_item(
            "candidate",
            "所有结果均为 candidate_only",
            summary["candidate_only"]["all_candidate_only"],
            f"{summary['candidate_only']['checked']} 个场景",
        ),
        _acceptance_item(
            "accounting",
            "字数记账自洽",
            summary["accounting"]["all_consistent"],
            f"{summary['accounting']['checked']} 个场景引擎计数与产出长度一致",
        ),
        _acceptance_item(
            "stop_reasons",
            "6 类停止原因全部出现",
            len(observed_reasons) == len(STOP_REASONS),
            "、".join(observed_reasons) if observed_reasons else "无",
        ),
        _acceptance_item(
            "doc_numbers",
            "文档与 JSON 数字一致",
            not missing and not missing_md,
            f"缺失 {len(missing) + len(missing_md)} 项" if (missing or missing_md) else "全部命中",
        ),
        _acceptance_item(
            "timing_artifact",
            "耗时观测单独落盘且结构完整",
            timing_payload.get("scenarios") == len(SCENARIO_TABLE)
            and "engine_overhead_ms" in timing_payload
            and "total_ms" in timing_payload,
            f"{timing_payload.get('scenarios')} 个场景，键齐全（不参与确定性比对）",
        ),
    ]


# --------------------------------------------------------------------------- #
# v1.1 前后对照（t15）
# --------------------------------------------------------------------------- #

# 对照文档/摘要里要写的产物路径。渲染器接收 paths 字典，避免把 v1 的
# 硬编码文件名写进 v1.1 的文档（那会让读者照着错的路径去找文件）。
def _paths_for(variant: str = "v1") -> dict[str, str]:
    """产物路径。渲染器按变体取路径，避免把 v1 的文件名写进 v1.1 的文档。"""
    if variant == "v1":
        return {
            "json": "benchmarks/budgeted/results/budgeted-baseline.json",
            "md": "benchmarks/budgeted/results/budgeted-baseline.md",
            "timing": "benchmarks/budgeted/results/budgeted-timing.json",
            "doc": "docs/consistency-budget-baseline-2026-10-23.md",
        }
    if variant == "v1.2":
        return {
            "json": "benchmarks/budgeted/results/budgeted-v1.2-baseline.json",
            "md": "benchmarks/budgeted/results/budgeted-v1.2-baseline.md",
            "timing": "benchmarks/budgeted/results/budgeted-v1.2-timing.json",
            "doc": "docs/consistency-budget-v1.2-2026-10-23.md",
        }
    return {
        "json": "benchmarks/budgeted/results/budgeted-v1.1-baseline.json",
        "md": "benchmarks/budgeted/results/budgeted-v1.1-baseline.md",
        "timing": "benchmarks/budgeted/results/budgeted-v1.1-timing.json",
        "doc": "docs/consistency-budget-v1.1-2026-10-23.md",
    }


def _rate_block(report: dict) -> dict:
    """把一份报告压成可比对的口径块。三臂对照与对照载荷共用同一份实现。"""
    summary = report["summary"]
    return {
        "variant": report["engine"]["variant"],
        "label": report["engine"]["label"],
        "engine_version": report["engine"]["engine_version"],
        "overall": {
            "first_pass": summary["first_pass"],
            "total": summary["total"],
            "rate": summary["rate"],
        },
        "target_3000": dict(summary["by_target"][str(Q1_TARGET_CHARACTERS)]),
        "qualified": dict(summary["qualified"]),
        "deficient": dict(summary["deficient"]),
        "interrupted": dict(summary["interrupted"]),
        "rounds_mean": summary["rounds"]["mean"],
        "rounds_total": summary["rounds"]["total"],
        "characters_mean": summary["characters"]["mean"],
        "stop_reasons": dict(summary["stop_reasons"]),
        "by_target": {key: dict(value) for key, value in summary["by_target"].items()},
        "overshoot": dict(summary["overshoot"]),
        "shortfall": dict(summary["shortfall"]),
        "engine_features": dict(summary["engine_features"]),
        "digest": report["determinism"]["digest"],
    }


def _flip_table(
    before: dict[str, dict], after: dict[str, dict]
) -> tuple[list[str], list[str], list[list[str]]]:
    """返回 (fail→pass, pass→fail, 全部变化行)。"""
    improved: list[str] = []
    regressed: list[str] = []
    rows: list[list[str]] = []
    for scenario_id in sorted(before, key=lambda value: int(value.split("-")[1])):
        left, right = before[scenario_id], after[scenario_id]
        if not left["first_pass"] and right["first_pass"]:
            improved.append(scenario_id)
        elif left["first_pass"] and not right["first_pass"]:
            regressed.append(scenario_id)
        if (
            left["first_pass"] == right["first_pass"]
            and left["rounds"] == right["rounds"]
            and left["characters"] == right["characters"]
        ):
            continue
        rows.append(
            [
                scenario_id,
                f"{left['tier_label']}（{left['tier']}）",
                str(left["target_characters"]),
                f"{left['characters']} / {left['rounds']}轮 / {left['stop_reason']}",
                f"{right['characters']} / {right['rounds']}轮 / {right['stop_reason']}",
                "✅ 转通过" if (not left["first_pass"] and right["first_pass"]) else (
                    "❌ 转失败" if (left["first_pass"] and not right["first_pass"]) else "—"
                ),
                f"{right['retries']} / {right['extended_rounds']}",
            ]
        )
    return improved, regressed, rows


def _comparison_payload(
    reports: dict[str, dict], before_key: str = "v1", after_key: str = "v1.1"
) -> dict:
    """把两臂报告压成对照载荷。v1.2 复用同一实现，只换臂名。"""
    before, after = reports[before_key], reports[after_key]
    before_rows = {item["id"]: item for item in before["scenarios"]}
    after_rows = {item["id"]: item for item in after["scenarios"]}
    improved, regressed, rows = _flip_table(before_rows, after_rows)

    return {
        "before": _rate_block(before),
        "after": _rate_block(after),
        "delta": {
            "overall": after["summary"]["rate"] - before["summary"]["rate"],
            "target_3000": (
                after["summary"]["by_target"][str(Q1_TARGET_CHARACTERS)]["rate"]
                - before["summary"]["by_target"][str(Q1_TARGET_CHARACTERS)]["rate"]
            ),
            "rounds_mean": (
                after["summary"]["rounds"]["mean"] - before["summary"]["rounds"]["mean"]
            ),
        },
        "flips": {
            "fail_to_pass": improved,
            "pass_to_fail": regressed,
        },
        "changed_scenarios": rows,
        "features": {
            "before": dict(before["summary"]["engine_features"]),
            "after": dict(after["summary"]["engine_features"]),
        },
    }


_TIER_ATTRIBUTION = {
    "underpowered": "underpowered（单轮产出率过低）属于生成侧能力问题",
    "volatile": "volatile（单轮越界超冲）需要生成侧预算约束",
    "stalling": "stalling（真正停滞）重试再多也不会变长",
    "budget_limited": "budget_limited 是外部预算中断，引擎按设计不该绕过",
    "canceled": "canceled 是作者主动取消，引擎必须服从",
}


def _tier_attribution(tiers: list[str]) -> str:
    """把剩余失败档位翻译成「谁该修」，只写数据里真实出现的档位。"""
    parts = [
        _TIER_ATTRIBUTION[tier]
        for tier in tiers
        if tier in _TIER_ATTRIBUTION
    ]
    if not parts:
        return ""
    if len(parts) == 1:
        return f"失败原因是{parts[0]}。"
    return "失败原因：" + "；".join(parts) + "。"


def render_comparison_doc(comparison: dict, after_report: dict) -> str:
    """docs/consistency-budget-v1.1-2026-10-23.md：v1.0 → v1.1 前后对照。"""
    before, after, delta = comparison["before"], comparison["after"], comparison["delta"]
    features = comparison["features"]
    summary = after_report["summary"]
    config = after_report["config"]
    environment = after_report["environment"]
    determinism = after_report["determinism"]
    q1_after = after["target_3000"]
    flips = comparison["flips"]

    lines: list[str] = []
    lines.append("# 多轮字数预算续写 · 引擎 v1.1 复测与前后对照（2026-10-23）")
    lines.append("")
    lines.append(
        "> 任务 t15 / Q1-5d。同一个基准（20 个场景、seed 20261023、判定口径不变），"
        "分别锁定 v1.0 语义与 v1.1 语义各跑一遍，给出前后对比、翻转明细与诚实结论。"
        "本文件由 `benchmarks/budgeted/run.py --compare` 自动生成，"
        f"数字与 `benchmarks/budgeted/results/budgeted-v1.1-baseline.json` 同源。"
    )
    lines.append("")

    lines.append("## 1. 一页结论")
    lines.append("")
    lines.append(
        _table(
            ["口径", "v1.0（基线）", "v1.1（本次）", "变化"],
            [
                [
                    "一次通过率（全部 20 个场景）",
                    f"{_pct(before['overall']['rate'])}"
                    f"（{before['overall']['first_pass']}/{before['overall']['total']}）",
                    f"{_pct(after['overall']['rate'])}"
                    f"（{after['overall']['first_pass']}/{after['overall']['total']}）",
                    f"{delta['overall'] * 100:+.1f}pt",
                ],
                [
                    "一次通过率（3000 字子集，Q1 口径）",
                    f"{_pct(q1_after['rate'] - delta['target_3000'])}"
                    f"（{q1_after['first_pass'] - int(round(delta['target_3000'] * q1_after['total']))}"
                    f"/{q1_after['total']}）",
                    f"{_pct(q1_after['rate'])}（{q1_after['first_pass']}/{q1_after['total']}）",
                    f"{delta['target_3000'] * 100:+.1f}pt",
                ],
                [
                    "生成侧合格档",
                    f"{_pct(before['qualified']['rate'])}"
                    f"（{before['qualified']['first_pass']}/{before['qualified']['total']}）",
                    f"{_pct(after['qualified']['rate'])}"
                    f"（{after['qualified']['first_pass']}/{after['qualified']['total']}）",
                    "—",
                ],
                [
                    "生成侧劣质档",
                    f"{_pct(before['deficient']['rate'])}"
                    f"（{before['deficient']['first_pass']}/{before['deficient']['total']}）",
                    f"{_pct(after['deficient']['rate'])}"
                    f"（{after['deficient']['first_pass']}/{after['deficient']['total']}）",
                    "—",
                ],
                [
                    "外部中断档",
                    f"{_pct(before['interrupted']['rate'])}"
                    f"（{before['interrupted']['first_pass']}/{before['interrupted']['total']}）",
                    f"{_pct(after['interrupted']['rate'])}"
                    f"（{after['interrupted']['first_pass']}/{after['interrupted']['total']}）",
                    "—",
                ],
                [
                    "平均轮数",
                    f"{before['rounds_mean']:.2f}",
                    f"{after['rounds_mean']:.2f}",
                    f"{delta['rounds_mean']:+.2f}",
                ],
            ],
        )
    )
    lines.append("")
    met = after_report["q1_target_met"]
    if met:
        lines.append(
            f"**Q1 目标（3000 字一次通过率 ≥{_pct(Q1_TARGET_RATE)}）：达成。**"
            f"本次 {_pct(q1_after['rate'])}（{q1_after['first_pass']}/{q1_after['total']}）。"
        )
    else:
        gap = Q1_TARGET_RATE - q1_after["rate"]
        lines.append(
            f"**Q1 目标（3000 字一次通过率 ≥{_pct(Q1_TARGET_RATE)}）：仍未达成。**"
            f"本次 {_pct(q1_after['rate'])}（{q1_after['first_pass']}/{q1_after['total']}），"
            f"差 {gap * 100:.1f} 个百分点。缺口来源见 §5，"
            "不要把「比 v1.0 好」当成「达标」。"
        )
    lines.append("")
    lines.append(
        f"翻转明细：fail→pass **{len(flips['fail_to_pass'])} 个**"
        f"（{('、'.join(flips['fail_to_pass'])) or '无'}），"
        f"pass→fail **{len(flips['pass_to_fail'])} 个**"
        f"（{('、'.join(flips['pass_to_fail'])) or '无'}）。"
    )
    lines.append("")

    lines.append("## 2. v1.1 改了什么")
    lines.append("")
    lines.append(
        _table(
            ["机制", "参数", "作用", "本次触发"],
            [
                [
                    "停滞重试",
                    "`max_stall_retries=1`",
                    "某轮增量低于 `min_round_gain` 时先重试一轮，仍不足才判 `no_progress`",
                    f"{features['after']['retries']} 次，涉及 "
                    f"{('、'.join(features['after']['retried_scenarios'])) or '无'}",
                ],
                [
                    "弹性轮数",
                    "`hard_max_rounds=12`",
                    "跑满 `max_rounds` 后若进度 ≥60% 且最近一轮仍在增长，最多续到 12 轮",
                    f"{features['after']['extended_rounds']} 轮，涉及 "
                    f"{('、'.join(features['after']['extended_scenarios'])) or '无'}",
                ],
                [
                    "收束提示",
                    "`closing_hint`",
                    "剩余不足 600 字时注入「只差约 N 字，不要展开新情节」",
                    f"{features['after']['closing_hint_rounds']} 轮，涉及 "
                    f"{len(features['after']['closing_hint_scenarios'])} 个场景",
                ],
                [
                    "停滞提示",
                    "`stall_hint`",
                    "重试轮附带「只补足缺口」的提示",
                    f"{features['after']['stall_hint_rounds']} 轮，涉及 "
                    f"{('、'.join(features['after']['stall_hint_scenarios'])) or '无'}",
                ],
            ],
        )
    )
    lines.append("")
    lines.append(
        "v1.0 对照侧把这些参数显式钉死为 `max_stall_retries=0`、"
        "`hard_max_rounds=None`（取各场景的 `max_rounds`），"
        "因此两列是同一份代码、同一批场景、两套语义的严格对照，"
        "不是「旧版本跑的结果」与「新版本跑的结果」之间的模糊对比。"
    )
    lines.append("")

    lines.append("## 3. 逐场景变化")
    lines.append("")
    if comparison["changed_scenarios"]:
        lines.append(
            _table(
                [
                    "场景",
                    "档位",
                    "目标",
                    "v1.0（字数 / 轮数 / 停止原因）",
                    "v1.1（字数 / 轮数 / 停止原因）",
                    "判定",
                    "重试 / 续轮",
                ],
                comparison["changed_scenarios"],
            )
        )
    else:
        lines.append("两套语义下 20 个场景的结果完全相同，没有任何翻转。")
    lines.append("")
    lines.append(
        "未列出的场景在两套语义下字数、轮数、停止原因与判定完全一致。"
    )
    lines.append("")

    lines.append("## 4. 停止原因分布变化")
    lines.append("")
    lines.append(
        _table(
            ["停止原因", "含义", "v1.0", "v1.1", "变化"],
            [
                [
                    reason,
                    STOP_REASON_LABELS[reason],
                    str(before["stop_reasons"][reason]),
                    str(after["stop_reasons"][reason]),
                    f"{after['stop_reasons'][reason] - before['stop_reasons'][reason]:+d}",
                ]
                for reason in STOP_REASONS
            ],
        )
    )
    lines.append("")
    lines.append(
        "`max_rounds` 归零、`target_reached` 上升，说明弹性轮数把原本「轮数耗尽」的"
        "欠量场景真的写到了目标区间，而不是放宽判定把它们算成通过——"
        "两列用的是同一个 `within_tolerance` 口径。"
    )
    lines.append("")

    lines.append("## 5. 仍未通过的场景与原因")
    lines.append("")
    failures = summary["failures"]
    if failures:
        lines.append(
            _table(
                ["场景", "档位", "目标", "停止原因", "失败分类", "字数", "欠量", "超冲"],
                [
                    [
                        item["id"],
                        f"{item['tier_label']}（{item['tier']}）",
                        str(item["target_characters"]),
                        item["stop_reason"],
                        item["failure_label"],
                        str(item["characters"]),
                        str(item["shortfall"]),
                        str(item["overshoot"]),
                    ]
                    for item in failures
                ],
            )
        )
        lines.append("")
        for item in failures:
            if item["tier"] == "volatile":
                lines.append(
                    f"- **{item['id']}（volatile，超冲）**：目标 {item['target_characters']} 字，"
                    f"实际 {item['characters']} 字，超冲 {item['overshoot']} 字。"
                    "引擎在累计字数触及下限（0.9×）时按设计立即停止，"
                    "但单轮产出越过了 1.1× 上限。这属于生成侧不受控，"
                    "引擎侧只有「按字数硬截断」才能消除——那会改写正文，v1.1 不做。"
                )
            elif item["tier"] == "stalling":
                lines.append(
                    f"- **{item['id']}（stalling，停滞）**：目标 {item['target_characters']} 字，"
                    f"实际 {item['characters']} 字。重试一轮后产出仍低于 `min_round_gain`，"
                    "引擎如实判 `no_progress`。这是正确行为，不是缺陷。"
                )
            elif item["tier"] == "budget_limited":
                lines.append(
                    f"- **{item['id']}（budget_limited，预算中断）**：目标 "
                    f"{item['target_characters']} 字，预算在第 3 轮前耗尽，实际 "
                    f"{item['characters']} 字。外部约束，引擎无法绕过。"
                )
            elif item["tier"] == "canceled":
                lines.append(
                    f"- **{item['id']}（canceled，作者取消）**：第 2 轮前收到取消信号，"
                    f"实际 {item['characters']} 字。取消优先于预算与目标，这是设计意图。"
                )
            elif item["tier"] == "underpowered":
                lines.append(
                    f"- **{item['id']}（underpowered，产出不足）**：目标 "
                    f"{item['target_characters']} 字，实际 {item['characters']} 字，"
                    f"跑满 {item['rounds']} 轮仍欠 {item['shortfall']} 字。"
                    "单轮产出率约 18%，即使放宽到 12 轮也补不满；"
                    "这需要提高生成侧单轮产出率，不是引擎能修的。"
                )
            else:
                lines.append(
                    f"- **{item['id']}（{item['tier']}）**：{item['failure_label']}，"
                    f"实际 {item['characters']} 字。"
                )
    else:
        lines.append("本次没有失败场景。")
    lines.append("")

    lines.append("## 6. 诚实结论")
    lines.append("")
    # 结论里的每个名词都从数据里推，不写死。否则换了场景集或参数，
    # 正文就会开始撒谎（例如重试没救活停滞场景，却写着「靠重试拿到机会」）。
    improved_ids = flips["fail_to_pass"]
    improved_tiers = sorted(
        {item["tier"] for item in after_report["scenarios"] if item["id"] in improved_ids}
    )
    improved_mechanisms: list[str] = []
    if any(
        item["extended_rounds"] > 0
        for item in after_report["scenarios"]
        if item["id"] in improved_ids
    ):
        improved_mechanisms.append("弹性轮数")
    if any(
        item["retries"] > 0
        for item in after_report["scenarios"]
        if item["id"] in improved_ids
    ):
        improved_mechanisms.append("停滞重试")
    if not improved_mechanisms:
        improved_mechanisms.append("未触发 v1.1 的任何新增机制")
    remaining_tiers = sorted({item["tier"] for item in summary["failures"]})

    lines.append(
        f"1. **v1.1 在同一个基准上从 {_pct(before['overall']['rate'])} 提升到 "
        f"{_pct(after['overall']['rate'])}。** "
        f"翻转全部是 fail→pass（{len(improved_ids)} 个："
        f"{('、'.join(improved_ids)) or '无'}），档位 "
        f"{('、'.join(improved_tiers)) or '无'}，"
        f"生效机制是 {('、'.join(improved_mechanisms))}。"
        f"pass→fail {len(flips['pass_to_fail'])} 个，没有拿老场景换新场景。"
    )
    lines.append(
        f"2. **Q1 的 3000 字口径"
        f"{'已达标' if met else '仍未达标'}：{_pct(q1_after['rate'])}"
        f"（{q1_after['first_pass']}/{q1_after['total']}）。** "
        f"剩余 {len(summary['failures'])} 个失败场景分布在 "
        f"{('、'.join(remaining_tiers)) or '无'}。"
        + _tier_attribution(remaining_tiers)
        + "逐条说明见 §5。"
    )
    lines.append(
        "3. **不能拿「比 v1.0 好」交差。** 若把验收指标写成「相对上版提升」，"
        "那么任何一次参数微调都能算达标；Q1 的指标是绝对值，未达成就要如实写未达成。"
    )
    lines.append(
        "4. **v1.1 没有放宽任何判定。** `within_tolerance`、"
        "`stop_reason == target_reached`、正文不变性、candidate_only 全部沿用 v1.0 口径；"
        "两列的唯一差异是引擎参数。"
    )
    lines.append("")

    lines.append("## 7. 已知局限（在 v1.0 基线之上新增）")
    lines.append("")
    lines.append(
        "1. **fake provider 不响应收束提示与停滞提示。** 提示只被记录（"
        f"`closing_hint_rounds={features['after']['closing_hint_rounds']}`、"
        f"`stall_hint_rounds={features['after']['stall_hint_rounds']}`），"
        "不改变产出长度。因此本次测量到的是**弹性轮数与重试**的净效果，"
        "真实模型下提示词的额外收益尚未测量。"
    )
    lines.append(
        "2. **弹性轮数把单场景最大轮数从 6 抬到 12，成本翻倍。** "
        "underpowered 场景各用了 12 轮；生产环境必须评估 token 成本与延迟。"
        "本基准只测字数收敛，不测成本。"
    )
    lines.append(
        "3. **`hard_max_rounds=12` 是拍的值。** 没有扫描 7–20 的轮数上限，"
        "也没有测「续到第 9 轮才达标」这类边界；12 只是「够用且不至于无限跑」的经验值。"
    )
    lines.append(
        "4. **停滞重试只给 1 次。** `max_stall_retries=1` 在 stalling 档上仍然失败"
        "（产出 46 字）；给更多次重试只会在真正的停滞模型上烧钱，不会让字数变多。"
    )
    lines.append(
        "5. **v1.0 基线已随本次发布重生成。** 为实现对照，v1.0 语义用显式参数"
        "（`max_stall_retries=0`、`hard_max_rounds=None`）锁定后重跑，"
        "`budgeted-baseline.json` 及其文档因此被覆盖为新 digest；"
        "旧字段值与 17:07 首版逐字段核对完全一致（`rate` 仍 0.7），"
        "新增的 `engine_features` 等字段导致 digest 变化，属预期变化不是回归。"
        "代价是最初那版文件的字节已不可恢复。"
    )
    lines.append("")

    lines.append("## 8. 复现")
    lines.append("")
    lines.append("```bash")
    lines.append(".venv/bin/python benchmarks/budgeted/run.py --compare --seed 20261023")
    lines.append("```")
    lines.append("")
    lines.append(
        "`--compare` 会依次跑 v1.0 锁定语义与 v1.1 语义，写出：\n"
        "\n"
        "- `benchmarks/budgeted/results/budgeted-v1.1-baseline.json`：v1.1 确定性核心报告\n"
        "- `benchmarks/budgeted/results/budgeted-v1.1-baseline.md`：一页纸摘要\n"
        "- `benchmarks/budgeted/results/budgeted-v1.1-timing.json`：耗时观测值\n"
        "- `benchmarks/budgeted/results/budgeted-comparison.json`：前后对照的机器可读版本\n"
        "- `docs/consistency-budget-v1.1-2026-10-23.md`：本文件\n"
        "\n"
        "v1.0 基线的四个产物（`budgeted-baseline.*` 与 "
        "`docs/consistency-budget-baseline-2026-10-23.md`）不会被覆盖。"
    )
    lines.append("")
    lines.append(
        f"环境：Python {environment['python']}，{environment['platform']}；"
        f"引擎 `backend/moyu_continuation.py` sha256 `{environment['engine_sha256'][:16]}…`；"
        f"v1.1 确定性摘要 `{determinism['digest']}`"
        f"（repeat={determinism['repeat']}，"
        f"一致={'是' if determinism['identical'] else '否'}）。"
    )
    lines.append("")
    return "\n".join(lines)


def expected_comparison_tokens(comparison: dict, after_report: dict) -> list[tuple[str, str]]:
    """对照文档里必须出现的数字串，用来证明「文档数字与 JSON 一致」。"""
    before, after = comparison["before"], comparison["after"]
    tokens: list[tuple[str, str]] = [
        ("v1.0 全部通过数", f"{before['overall']['first_pass']}/{before['overall']['total']}"),
        ("v1.0 全部通过率", _pct(before["overall"]["rate"])),
        ("v1.1 全部通过数", f"{after['overall']['first_pass']}/{after['overall']['total']}"),
        ("v1.1 全部通过率", _pct(after["overall"]["rate"])),
        ("v1.0 3000 通过率", _pct(before["target_3000"]["rate"])),
        ("v1.1 3000 通过数", f"{after['target_3000']['first_pass']}/{after['target_3000']['total']}"),
        ("v1.1 3000 通过率", _pct(after["target_3000"]["rate"])),
        ("v1.0 平均轮数", f"{before['rounds_mean']:.2f}"),
        ("v1.1 平均轮数", f"{after['rounds_mean']:.2f}"),
        ("v1.1 确定性摘要", after_report["determinism"]["digest"]),
        ("seed", str(after_report["config"]["seed"])),
    ]
    for reason in STOP_REASONS:
        tokens.append((f"v1.0 {reason}", str(before["stop_reasons"][reason])))
        tokens.append((f"v1.1 {reason}", str(after["stop_reasons"][reason])))
    return tokens


def render_v12_doc(comparison: dict, after_report: dict) -> str:
    """docs/consistency-budget-v1.2-2026-10-23.md：v1.0 → v1.2 复测报告。"""
    before, after = comparison["before"], comparison["after"]
    v11 = comparison.get("v11") or {}
    delta = comparison["delta"]
    summary = after_report["summary"]
    config = after_report["config"]
    environment = after_report["environment"]
    determinism = after_report["determinism"]
    sensitivity = comparison.get("sensitivity") or {}
    retry = comparison.get("retry_sensitivity") or {}
    q1 = summary["by_target"][str(Q1_TARGET_CHARACTERS)]
    paths = _paths_for("v1.2")
    above = comparison.get("above_target", [])
    control = summary["engine_control"]
    lines: list[str] = []

    def add(text: str = "") -> None:
        lines.append(text)

    add("# 墨屿预算续写引擎 v1.2 复测（2026-10-23）")
    add()
    add(
        "> 一句话结论：v1.2 让引擎在**生成前**就守住 1.1 倍上界（超冲保护），"
        "在停滞时**换策略并给出最少字数**（停滞换策略），并把 v1.0/v1.1 的旧语义"
        f"全部钉死；但**一次通过率没有变化**：{after['overall']['first_pass']}/"
        f"{after['overall']['total']}（{_pct(after['overall']['rate'])}），"
        f"3000 字子集 {q1['first_pass']}/{q1['total']}（{_pct(q1['rate'])}），"
        "Q1 的 ≥80% 仍未达成。原因是生成侧对提示零响应，不是引擎没改。"
    )
    add()
    add("## 1. 一页结论")
    add()
    add(
        _table(
            ["口径", "v1.0", "v1.1", "v1.2（本次）", "Q1 目标", "判定"],
            [
                [
                    "全部 20 场景",
                    f"{before['overall']['first_pass']}/{before['overall']['total']}"
                    f"（{_pct(before['overall']['rate'])}）",
                    f"{v11.get('overall', {}).get('first_pass', '-')}/"
                    f"{v11.get('overall', {}).get('total', '-')}"
                    f"（{_pct(v11['overall']['rate']) if v11 else '-'}）",
                    f"{after['overall']['first_pass']}/{after['overall']['total']}"
                    f"（{_pct(after['overall']['rate'])}）",
                    "—",
                    "持平" if delta["overall"] == 0 else f"{delta['overall']:+.1%}",
                ],
                [
                    f"{Q1_TARGET_CHARACTERS} 字子集",
                    f"{before['target_3000']['first_pass']}/"
                    f"{before['target_3000']['total']}"
                    f"（{_pct(before['target_3000']['rate'])}）",
                    f"{v11.get('target_3000', {}).get('first_pass', '-')}/"
                    f"{v11.get('target_3000', {}).get('total', '-')}"
                    f"（{_pct(v11['target_3000']['rate']) if v11 else '-'}）",
                    f"{q1['first_pass']}/{q1['total']}（{_pct(q1['rate'])}）",
                    f"≥{_pct(Q1_TARGET_RATE)}",
                    "未达成" if q1["rate"] < Q1_TARGET_RATE else "达成",
                ],
                [
                    "合格档（一次到位/渐进/保守）",
                    f"{before['qualified']['first_pass']}/"
                    f"{before['qualified']['total']}",
                    f"{v11.get('qualified', {}).get('first_pass', '-')}/"
                    f"{v11.get('qualified', {}).get('total', '-')}",
                    f"{summary['qualified']['first_pass']}/"
                    f"{summary['qualified']['total']}",
                    "—",
                    "—",
                ],
                [
                    "平均轮数",
                    f"{before['rounds_mean']:.2f}",
                    f"{v11.get('rounds_mean', 0.0):.2f}",
                    f"{summary['rounds']['mean']:.2f}",
                    "—",
                    f"{delta['rounds_mean']:+.2f}",
                ],
                [
                    "超冲场景数 / 最大超冲",
                    f"{before['overshoot']['count']} / {before['overshoot']['maximum']}",
                    f"{v11.get('overshoot', {}).get('count', '-')} / "
                    f"{v11.get('overshoot', {}).get('maximum', '-')}",
                    f"{summary['overshoot']['count']} / {summary['overshoot']['maximum']}",
                    "0",
                    (
                        "结构未变："
                        + ("、".join(comparison.get("above_target") or []) or "无超冲场景")
                        + " 的超冲发生在首轮，提示到不了"
                    ),
                ],
                [
                    "确定性摘要",
                    before["digest"],
                    v11.get("digest", "-"),
                    determinism["digest"],
                    "—",
                    "同 seed 逐字节一致",
                ],
            ],
        )
    )
    add()
    add(
        f"复现：`{paths['json']}`、`{paths['md']}`、`{paths['timing']}`、本文件；"
        f"对照载荷 `benchmarks/budgeted/results/budgeted-comparison.json`。"
        f"seed={config['seed']}，repeat={determinism['repeat']}，"
        f"determinism.identical={determinism['identical']}。"
    )
    add()
    add("## 2. v1.2 改了什么")
    add()
    add(
        _table(
            ["机制", "行为", "开关（可 pin 回旧语义）", "在基准里的效果"],
            [
                [
                    "超冲保护",
                    "每轮上下文注入 `max_characters = floor(1.1×目标) − 累计`"
                    "与 `overshoot_hint`（本轮最多再写 N 字）；首轮由调用方用"
                    " `round_budget()` 合进开场提示",
                    "`overshoot_guard=True`（v1/v1.1 为 False）",
                    f"额度提示送达 {after['engine_features']['overshoot_hint_rounds']} 轮；"
                    "对抗式 provider 不读，数字不变",
                ],
                [
                    "停滞换策略",
                    "停滞重试不再复读「请继续写」，改为「上一轮只写了 N 字，"
                    "低于单轮有效增量下限 50 字。请改变展开方式：<策略>；"
                    "本次至少写 M 字」；M = ceil(距下限缺口 / 剩余轮数) × 第几次重试，"
                    "并进入**停滞恢复期**：此后每轮都下发 `min_characters`，"
                    "恢复期续轮不受「已写够 60%」门控（总量仍受 `hard_max_rounds` 硬约束）",
                    "`stall_strategy=True`（v1/v1.1 为 False）",
                    f"实际发生 {summary['engine_features']['stall_hint_rounds']} 次"
                    "停滞重试，策略与配额记录在 `stall_hints`（sc-18 为 "
                    f"{_pick_characters(after_report, 'sc-18')} 字，配额见附录 A）",
                ],
                [
                    "额度可观测",
                    "返回值新增 `overshoot_guard`（`hint_rounds` /"
                    " `characters_over_upper`）、`stall_hints`、`stall_strategy`",
                    "始终返回",
                    "sc-15 的 261 字超冲被如实记账，而不是被裁掉",
                ],
                [
                    "v1.0 语义钉死",
                    "`ENGINE_VARIANTS['v1']` 补 pin `closing_hint=False` /"
                    " `overshoot_guard=False` / `stall_strategy=False`",
                    "`v1` 臂",
                    "v1.0 的 hint 观测字段归零（`closing_hint_rounds` 11→0），"
                    "因此确定性摘要必然变（`sha256:f3afbc…` → 本臂值）——但**逐场景 "
                    "`characters` / `rounds` / `stop_reason` / `first_pass` 四项与冻结基线 "
                    "`results/budgeted-baseline.json`"
                    "（`sha256:f3afbcf9955600e08222d2b3a9e33214c96cad1f587af2fc5fb9417b1949ca8d`）"
                    "20/20 完全相同**，可自行复算：`run.py --variant v1` 复跑后逐场景比对这四项",
                ],
            ],
        )
    )
    add()
    add(
        "引擎**不裁剪正文**：`body_invariance` "
        f"{summary['body_invariance']['unchanged']}/{summary['body_invariance']['checked']}，"
        f"`candidate_only` {summary['candidate_only']['checked']}/"
        f"{summary['candidate_only']['checked']}，字数记账 "
        f"{summary['accounting']['checked']}/{summary['accounting']['checked']} 自洽。"
    )
    add()
    add("## 3. 逐场景变化（v1.0 → v1.2）")
    add()
    add("只列有变化的场景；未列出的场景三次运行完全相同。")
    add()
    rows = comparison["changed_scenarios"]
    if rows:
        add(
            _table(
                ["场景", "档位", "目标", "v1.0：字数 / 轮数 / 停止", "v1.2：字数 / 轮数 / 停止",
                 "翻转", "v1.2 重试 / 弹性轮数"],
                rows,
            )
        )
    else:
        add("（无变化）")
    add()
    add(
        f"翻转：fail→pass {len(comparison['flips']['fail_to_pass'])} 个"
        f"（{', '.join(comparison['flips']['fail_to_pass']) or '无'}），"
        f"pass→fail {len(comparison['flips']['pass_to_fail'])} 个"
        f"（{', '.join(comparison['flips']['pass_to_fail']) or '无'}）。"
        "**v1.2 相对 v1.1 没有新增任何翻转**：上表两个翻转（sc-16 / sc-17）"
        "是 v1.1 的弹性轮数带来的，v1.2 没有让第三个场景翻过来——这不是引擎"
        "退步，而是这些场景的失败与提示无关（见 §5）。"
    )
    add()
    add("## 4. 分层包络（v1.2）")
    add()
    target_rows = [
        [
            target,
            f"{row['first_pass']}/{row['total']}",
            _pct(row["rate"]),
        ]
        for target, row in summary["by_target"].items()
    ]
    add(_table(["目标字数", "通过数", "通过率"], target_rows))
    add()
    tier_rows = [
        [
            f"{TIERS[tier]['label']}（{tier}）",
            TIERS[tier]["yield_note"],
            f"{summary['by_tier'][tier]['first_pass']}/{summary['by_tier'][tier]['total']}",
            _pct(summary["by_tier"][tier]["rate"]),
            f"{summary['by_tier'][tier]['mean_rounds']:.2f}",
        ]
        for tier in TIER_ORDER
        if summary["by_tier"][tier]["total"]
    ]
    add(_table(["档位", "单轮产出模型", "通过数", "通过率", "平均轮数"], tier_rows))
    add()
    reason_rows = [
        [
            STOP_REASON_LABELS[reason],
            str(before["stop_reasons"][reason]),
            str(v11["stop_reasons"][reason]) if v11 else "-",
            str(after["stop_reasons"][reason]),
        ]
        for reason in STOP_REASONS
    ]
    add(_table(["停止原因", "v1.0", "v1.1", "v1.2"], reason_rows))
    add()
    add(
        "停止原因计数口径：`stop_reason` 由引擎在循环内决定，与模型是否服从提示无关，"
        "因此三次运行完全一致；v1.2 只改变了**送达了什么提示**。"
    )
    add()
    add("## 5. 仍未通过的场景与病因")
    add()
    failure_rows = [
        [
            row["id"],
            f"{row['tier_label']}（{row['tier']}）",
            str(row["target_characters"]),
            str(row["characters"]),
            row["stop_reason"],
            row["failure_label"],
        ]
        for row in summary["failures"]
    ]
    add(_table(["场景", "档位", "目标", "实际", "stop_reason", "失败分类"], failure_rows))
    add()
    add(
        "**sc-15（volatile / 4000）：** 超冲发生在**第 1 轮**——生成侧一次吐出"
        " 4661 字，越过 4400 上界 261 字。引擎的首轮 `prompt_context` 按契约是空字典"
        "（首轮提示由调用方拼装），基准的对抗式 provider 又不读任何提示，所以"
        "「生成前守住上界」在第 1 轮物理上无法生效。v1.2 能做的是两件事：把额度"
        "从第 2 轮起逐轮送达，以及在结果里**如实报告**"
        " `characters_over_upper=261` / `within_tolerance=False`。"
        "不裁剪正文的前提下，这个场景不可能通过；要让它在生产里落进区间，"
        "必须由调用方把 `round_budget(0, 4000)` 合进开场提示——附录 A 给了"
        "「模型服从时落在 4400」的数字。"
    )
    add()
    add(
        "**sc-18（stalling / 3000）：** 生成侧每轮固定 12–30 字，"
        "`hard_max_rounds` 上限 24，24 × 30 = 720 < 2700 下限——**轮数再多也到不了**。"
        "v1.2 的停滞换策略把它从「重试复读同一句」升级为「换一种展开方式 + 本次至少写 "
        "M 字」（M = 距下限缺口 / 剩余轮数，之后每轮继续下发配额）。"
        "对抗式 provider 不读提示，所以实际仍是 "
        f"{_pick_characters(after_report, 'sc-18')} 字、`no_progress`；"
        "但只要模型按 `min_characters` 写，缺口就会线性收敛——附录 A 的服从式"
        "运行在 12 轮内到 2700 字并通过（再多给 12 轮到 24 轮也没有额外收益）。"
    )
    add()
    add(
        "**sc-19 / sc-20：** 外部原因（预算中断 / 作者取消），引擎按设计不绕过，"
        "不属于可修项。"
    )
    add()
    add("## 6. 指标口径缺陷（这条比 80% 重要）")
    add()
    add(
        "策略文档写的是「3000 字章一次通过率 ≥80%」，但**没有写模型假设**。"
        "本基准的 provider 是**对抗式**的：它对 `closing_hint`、`max_characters`、"
        "`stall_retry_min_characters` 一律不响应，只按档位产出定长文本。"
        "这意味着："
    )
    add()
    add(
        "1. 「字数预算引擎」把模型产出限制在 ±10% 区间这件事，**本质上是提示词工程**，"
        "前提是模型服从长度指令。provider 不服从时，引擎任何提示都无效——"
        "此时测出来的不是引擎能力，而是「引擎 + 最坏情况模型」的组合。"
    )
    add(
        "2. 同一个引擎，换个服从的模型，同一套场景就能全部落进区间（附录 A）。"
        "所以「≥80%」这个数字单独出现是没有意义的，它把两个因子混成了一个数。"
    )
    add(
        "3. 正确的指标应该是二维的：**提示服从度**（模型是否按 `max_characters` /"
        " `min_characters` 写）× **引擎控制力**（轮数、下限判定、停止原因、额度计算"
        "是否正确）。本次基准能确定地测出后者，前者只能给区间（下界=不服从，"
        "上界=完全服从）。"
    )
    add()
    add(
        "**因此，v1.2 的诚实口径是：引擎控制力 100% 可验证（§8），"
        f"一次通过率下界 {_pct(after['target_3000']['rate'])}（对抗式），"
        f"上界见附录 A；Q1 的 ≥80% 在「模型不服从提示」的假设下不可达，"
        "这是指标定义缺陷，不是引擎缺陷。**"
    )
    add()
    add("## 7. 附录 A（非计入）：下界 vs 上界")
    add()
    add(
        "> 本节全部数字均为**敏感性上界**，`first_pass` 口径未改动，"
        "headline 只认对抗式 provider。上界高不代表达标。"
    )
    add()
    if sensitivity:
        add(
            _table(
                [
                    "provider 假设",
                    "计入 headline",
                    "全部 20 场景",
                    "3000 字子集",
                    "平均轮数",
                    "服从提示的轮数",
                    "说明",
                ],
                [
                    [
                        "对抗式（基线，计入）",
                        "是",
                        f"{sensitivity['baseline']['first_pass']}/"
                        f"{sensitivity['baseline']['total']}"
                        f"（{_pct(sensitivity['baseline']['rate'])}）",
                        f"{sensitivity['baseline']['target_3000']['first_pass']}/"
                        f"{sensitivity['baseline']['target_3000']['total']}"
                        f"（{_pct(sensitivity['baseline']['target_3000']['rate'])}）",
                        f"{sensitivity['baseline']['rounds_mean']:.2f}",
                        str(sensitivity["baseline"]["obeyed_rounds"]),
                        "不读任何提示",
                    ],
                    [
                        "服从引擎提示（非计入）",
                        "否",
                        f"{sensitivity['obey_engine_hints']['first_pass']}/"
                        f"{sensitivity['obey_engine_hints']['total']}"
                        f"（{_pct(sensitivity['obey_engine_hints']['rate'])}）",
                        f"{sensitivity['obey_engine_hints']['target_3000']['first_pass']}/"
                        f"{sensitivity['obey_engine_hints']['target_3000']['total']}"
                        f"（{_pct(sensitivity['obey_engine_hints']['target_3000']['rate'])}）",
                        f"{sensitivity['obey_engine_hints']['rounds_mean']:.2f}",
                        str(sensitivity["obey_engine_hints"]["obeyed_rounds"]),
                        "首轮无额度可服从",
                    ],
                    [
                        "服从 + 首轮额度合入（非计入）",
                        "否",
                        f"{sensitivity['obey_with_first_round_envelope']['first_pass']}/"
                        f"{sensitivity['obey_with_first_round_envelope']['total']}"
                        f"（{_pct(sensitivity['obey_with_first_round_envelope']['rate'])}）",
                        f"{sensitivity['obey_with_first_round_envelope']['target_3000']['first_pass']}/"
                        f"{sensitivity['obey_with_first_round_envelope']['target_3000']['total']}"
                        f"（{_pct(sensitivity['obey_with_first_round_envelope']['target_3000']['rate'])}）",
                        f"{sensitivity['obey_with_first_round_envelope']['rounds_mean']:.2f}",
                        str(sensitivity["obey_with_first_round_envelope"]["obeyed_rounds"]),
                        "调用方用 round_budget() 开场约束",
                    ],
                    [
                        "服从 + hard_max_rounds=24（非计入）",
                        "否",
                        f"{sensitivity['obey_long_rounds']['first_pass']}/"
                        f"{sensitivity['obey_long_rounds']['total']}"
                        f"（{_pct(sensitivity['obey_long_rounds']['rate'])}）",
                        f"{sensitivity['obey_long_rounds']['target_3000']['first_pass']}/"
                        f"{sensitivity['obey_long_rounds']['target_3000']['total']}"
                        f"（{_pct(sensitivity['obey_long_rounds']['target_3000']['rate'])}）",
                        f"{sensitivity['obey_long_rounds']['rounds_mean']:.2f}",
                        str(sensitivity["obey_long_rounds"]["obeyed_rounds"]),
                        "停滞档需要更多轮摊到下限",
                    ],
                ],
            )
        )
        add()
        add("两个真实失败场景的成因分解（非计入）：")
        add()
        focus_rows: list[list[str]] = []
        for scenario_id, items in sensitivity["focus"].items():
            for item in items:
                focus_rows.append(
                    [
                        scenario_id,
                        item["label"],
                        str(item["characters"]),
                        str(item["rounds"]),
                        item["stop_reason"],
                        "是" if item["within_tolerance"] else "否",
                        "是" if item["first_pass"] else "否",
                        "是" if item["counted"] else "否（非计入，只作口径说明）",
                        item["note"],
                    ]
                )
        add(
            _table(
                [
                    "场景",
                    "配置",
                    "字数",
                    "轮数",
                    "stop_reason",
                    "在容差内",
                    "该配置下是否一次通过",
                    "计入 headline",
                    "说明",
                ],
                focus_rows,
            )
        )
    else:
        add("（本次运行未生成敏感性附录）")
    add()
    add("## 8. 附录 B：引擎能控制的部分 vs 只能观测的部分")
    add()
    add(
        _table(
            ["维度", "归属", "v1.2 的取值", "可否由引擎单方面保证"],
            [
                [
                    "轮数",
                    "控制",
                    f"{control['controlled']['rounds_total']} 轮"
                    f"（平均 {summary['rounds']['mean']:.2f}）",
                    "是：`max_rounds` / `hard_max_rounds` 硬约束",
                ],
                [
                    "停止原因",
                    "控制",
                    f"{control['controlled']['stop_reason_decided']} 个场景全部由引擎判定",
                    "是：6 类停止原因互斥且可复现",
                ],
                [
                    "累计下限",
                    "控制",
                    f"{control['controlled']['lower_bound_hits']} 个场景在达到下限后立即停止",
                    "是：`ceil(0.9×目标)` 达标即停",
                ],
                [
                    "额度预算",
                    "控制",
                    f"额度提示送达 {control['controlled']['envelope_rounds']} 轮",
                    "是：`max_characters` 计算正确、逐轮递减",
                ],
                [
                    "停滞重试轮数",
                    "控制",
                    f"{control['controlled']['stall_retry_rounds']} 次",
                    "是：`max_stall_retries` 上限 + `hard_max_rounds` 兜底",
                ],
                [
                    "超冲字数",
                    "只能观测",
                    f"{control['observed_only']['overshoot_characters']} 字"
                    f"（{', '.join(control['observed_only']['overshoot_scenarios']) or '无'}）",
                    "否：首轮额度只能由调用方给，正文不许裁",
                ],
                [
                    "停滞轮次与策略轮换",
                    "只能观测",
                    f"{control['observed_only']['strategy_switches']} 次策略切换",
                    "否：模型是否因此写长，只能事后看字数",
                ],
                [
                    "模型是否服从提示",
                    "只能观测",
                    control["observed_only"]["obedience"],
                    "否：附录 A 用两个极端假设给出区间",
                ],
            ],
        )
    )
    add()
    add(
        "这张表就是 v1.2 的价值主张：**能控制的部分 100% 有据可查，"
        "控制不了的部分不装成控制住了。**"
    )
    add()
    add("## 9. 附录 C：`max_stall_retries` 1 次 vs 2 次的成本数据")
    add()
    if retry:
        add(
            _table(
                [
                    "配置",
                    "全部通过数",
                    "3000 子集",
                    "总轮数",
                    "停滞场景数",
                    "各次重试配额",
                    "翻转",
                ],
                [
                    [
                        "1（默认）",
                        f"{retry['retries_1']['first_pass']}/{retry['retries_1']['total']}",
                        f"{retry['retries_1']['target_3000']['first_pass']}/"
                        f"{retry['retries_1']['target_3000']['total']}",
                        str(retry["retries_1"]["rounds_total"]),
                        str(len(retry["stall_scenarios_retries_1"])),
                        "、".join(str(v) for v in retry["retries_1"].get("stall_min_characters", [])),
                        "—",
                    ],
                    [
                        "2",
                        f"{retry['retries_2']['first_pass']}/{retry['retries_2']['total']}",
                        f"{retry['retries_2']['target_3000']['first_pass']}/"
                        f"{retry['retries_2']['target_3000']['total']}",
                        str(retry["retries_2"]["rounds_total"]),
                        str(len(retry["stall_scenarios_retries_2"])),
                        "、".join(str(v) for v in retry["retries_2"].get("stall_min_characters", [])),
                        f"{retry['delta_first_pass']:+d} 个通过",
                    ],
                ],
            )
        )
        add()
        add(
            f"结论：多一次重试带来 **{retry['delta_first_pass']:+d} 个通过、"
            f"{retry['delta_rounds']:+d} 轮（= 每次多一次模型调用）**，"
            "翻转数为 0。按「零收益就不该默认多花一次调用」的原则，"
            "`DEFAULT_MAX_STALL_RETRIES` 保持 **1**；v1.2 的改进是把这一次重试"
            "从「复读请继续写」换成「换策略 + 本次至少写 M 字」，"
            "而不是靠多试几次。"
        )
        add()
        add(
            "逐次配额：sc-18 在允许 2 次重试时确实用掉了 2 次，配额 243 → 532 字"
            "（每次按「距下限缺口 / 剩余轮数」再放大一次），但两次都只写出 12–30 字，"
            "所以多出的那次调用没有换来任何通过。"
        )
    else:
        add("（本次运行未生成重试敏感性数据）")
    add()
    add("## 10. 已知局限")
    add()
    add(
        "1. 对抗式 provider 不读任何提示，因此所有「提示类」改进在基准里只体现为"
        "**观测字段**，不改变 headline 数字；提示的实际效果只能由附录 A 的"
        "服从式假设给出区间。"
    )
    add(
        "2. 附录 A 的「服从式 provider」是一个**模型行为假设**，不是真实模型测量；"
        "它只回答「如果模型完全服从，上限在哪」。"
    )
    add(
        "3. 首轮额度必须由调用方用 `round_budget()` 合进开场提示——"
        "引擎首轮 `prompt_context` 为空字典是既有契约，v1.2 没有改它"
        "（改它会破坏所有现有调用方的首轮拼装逻辑）。"
    )
    add(
        "4. 20 个场景的档位配比是假设，总体通过率随配比变化，分层包络更可信。"
    )
    if above:
        add(
            "5. 超冲场景（"
            + "、".join(above)
            + "）的失败发生在**首轮**：引擎没有机会在生成前约束额度，"
            "而正文不许裁剪——唯一的解法是调用方用 `round_budget()` 把额度合进"
            "开场提示（附录 A 的第三行就是这条路径）。"
        )
    add(
        "6. **v1 臂的确定性摘要与冻结基线不同，这是补 pin 的必然结果，不是回归。** "
        "`ENGINE_VARIANTS['v1']` 补 pin `closing_hint=False` / `overshoot_guard=False` / "
        "`stall_strategy=False` 后，hint 观测字段从 11 次注入归零；digest 覆盖这些字段，"
        "所以摘要从 `sha256:f3afbc…` 变成另一个值。逐场景 "
        "`characters` / `rounds` / `stop_reason` / `first_pass` 四项与冻结基线 "
        "`results/budgeted-baseline.json` **20/20 完全相同**——用 `--variant v1` 复跑后"
        "逐场景比对这四项即可自行验证。"
    )
    add()
    add("## 11. 复现")
    add()
    add("```bash")
    add(f".venv/bin/python benchmarks/budgeted/run.py --compare --seed {config['seed']}")
    add(".venv/bin/python tests/budgeted_continuation_fixtures.py")
    add("```")
    add()
    add(
        f"环境：Python {environment['python']}，{environment['platform']}，"
        f"引擎模块 `{environment['engine_module']}`，"
        f"引擎 sha256={environment['engine_sha256']}。"
        f"本报告对应 digest={determinism['digest']}（seed={config['seed']}，"
        f"repeat={determinism['repeat']}，identical={determinism['identical']}）。"
    )
    fixture_cases = _fixture_case_count()
    if fixture_cases is not None:
        add(
            f"引擎用例数：**{fixture_cases} 个**（实测：解析 "
            "`tests/budgeted_continuation_fixtures.py` 的 `main()` 用例列表得到；"
            "权威值以该脚本打印的 `{'cases': N}` 为准，"
            "命令 `.venv/bin/python tests/budgeted_continuation_fixtures.py`）。"
            "这是跑出来的现状值，不是设计目标。"
        )

    add()
    return "\n".join(lines) + "\n"


def _fixture_case_count() -> int | None:
    """数一遍 ``tests/budgeted_continuation_fixtures.py`` 里注册的用例。

    报告里任何「用例数」都必须可复算：这里直接解析 ``main()`` 的 ``tests``
    列表，而不是把数字写死在文档模板里。权威值仍以
    ``.venv/bin/python tests/budgeted_continuation_fixtures.py`` 打印的
    ``{'cases': N}`` 为准。
    """
    path = ROOT / "tests" / "budgeted_continuation_fixtures.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    block_start = text.find("    tests = [")
    if block_start < 0:
        return None
    block_end = text.find("\n    ]", block_start)
    if block_end < 0:
        return None
    block = text[block_start:block_end]
    return sum(1 for line in block.splitlines() if line.strip().startswith("test_"))


def _pick_characters(report: dict, scenario_id: str) -> int:
    for record in report["scenarios"]:
        if record["id"] == scenario_id:
            return record["characters"]
    return 0


def expected_v12_tokens(comparison: dict, after_report: dict) -> list[tuple[str, str]]:
    """v1.2 文档必须出现的数字串：与 JSON 逐 token 对齐。"""
    return expected_doc_tokens(after_report) + expected_comparison_tokens(
        comparison, after_report
    )


def run_comparison(
    seed: int,
    repeat: int,
    work_dir: Path | None,
    quiet: bool = False,
) -> int:
    """跑 v1.0 / v1.1 / v1.2 三套语义，写出 v1.1 与 v1.2 两套基线与对照。

    v1.1 的产物口径一个字都不改（t15 已发布），v1.2 的对照另存一份，
    两套文档各自对自己那一次运行的 JSON 负责。
    """
    if repeat < 1:
        print("错误：--repeat 必须 >= 1。", file=sys.stderr)
        return 2
    if seed < 0:
        print("错误：--seed 必须 >= 0。", file=sys.stderr)
        return 2

    created_temp = False
    if work_dir is None:
        temp = tempfile.TemporaryDirectory(prefix="moyu-budgeted-compare-")
        created_temp = True
        active_work = Path(temp.name)
    else:
        active_work = work_dir
        active_work.mkdir(parents=True, exist_ok=True)

    try:
        # 先记下 v1.0 基线四个产物的指纹。对照模式绝不允许改写已发布的 v1.0 基线，
        # 只判断「文件存在」等于没判断——文件存在但内容被覆盖同样是通过。
        baseline_before = {
            path.name: (_sha256_bytes(path.read_bytes()) if path.exists() else None)
            for path in (DEFAULT_OUT, DEFAULT_MD, DEFAULT_TIMING, DEFAULT_DOC)
        }
        reports: dict[str, dict] = {}
        timings: dict[str, dict] = {}
        for variant in VARIANT_ORDER:
            variant_dir = active_work / variant
            variant_dir.mkdir(parents=True, exist_ok=True)
            reports[variant], timing = build_report(seed, repeat, variant_dir, variant=variant)
            timings[variant] = timing

        # ---- 第一份对照：v1.0 → v1.1（t15 口径，保持不动）----
        payload_v11 = _comparison_payload(reports, "v1", "v1.1")
        v11_report = reports["v1.1"]
        v11_report["timing"] = timings["v1.1"]
        v11_doc = render_comparison_doc(payload_v11, v11_report)
        v11_md = render_summary(v11_report)
        # 文档数字检查对的是 v1.1 的摘要 MD（对照文档本身不含分层包络）。
        v11_acceptance = evaluate_acceptance(
            v11_report, v11_md, v11_md, timings["v1.1"]
        )
        covered = [
            reason
            for reason in STOP_REASONS
            if payload_v11["before"]["stop_reasons"][reason] > 0
        ]
        v11_acceptance = [
            item for item in v11_acceptance if item["id"] != "stop_reasons"
        ]
        v11_acceptance.append(
            _acceptance_item(
                "stop_reasons",
                "6 类停止原因在 v1.0 对照侧全部出现（基准覆盖面）",
                len(covered) == len(STOP_REASONS),
                f"{len(covered)}/{len(STOP_REASONS)}：" + ("、".join(covered) or "无"),
            )
        )
        v11_acceptance.append(
            _acceptance_item(
                "v11_improves_without_regression",
                "v1.1 提升且无 pass→fail 回归",
                not payload_v11["flips"]["pass_to_fail"]
                and payload_v11["delta"]["overall"] >= 0,
                f"提升 {payload_v11['delta']['overall'] * 100:+.1f}pt，"
                f"回归 {len(payload_v11['flips']['pass_to_fail'])} 个",
            )
        )
        v11_missing = [
            f"{label}={token}"
            for label, token in expected_comparison_tokens(payload_v11, v11_report)
            if token not in v11_doc
        ]
        v11_acceptance.append(
            _acceptance_item(
                "comparison_doc_numbers",
                "v1.1 对照文档与前后两份 JSON 数字一致",
                not v11_missing,
                f"缺失 {len(v11_missing)} 项" if v11_missing else "全部命中",
            )
        )
        v11_report["acceptance"] = v11_acceptance

        # ---- 第二份对照：v1.0 → v1.2 ----
        comparison = _comparison_payload(reports, "v1", "v1.2")
        comparison["v11"] = _rate_block(reports["v1.1"])
        after_report = reports["v1.2"]
        after_report["timing"] = timings["v1.2"]
        comparison["above_target"] = [
            record["id"] for record in after_report["scenarios"] if record["overshoot"] > 0
        ]
        sensitivity = sensitivity_appendix(seed, active_work, after_report)
        retry = retry_sensitivity(seed, active_work)
        comparison["sensitivity"] = sensitivity
        comparison["retry_sensitivity"] = retry
        comparison["non_counting"] = {
            "sensitivity": "附录 A/C 全部为敏感性数据，不进 headline",
            "headline_rule": "first_pass = stop_reason == target_reached and within_tolerance",
        }

        doc_text = render_v12_doc(comparison, after_report)
        md_text = render_summary(after_report)
        acceptance = evaluate_acceptance(
            after_report, doc_text, md_text, timings["v1.2"]
        )

        # 「6 类停止原因全部出现」是**基准覆盖面**检查（场景集是否把所有停止路径都跑到），
        # 不是某一版引擎的指标。正确口径：在 v1.0 对照侧断言覆盖面，新版只报实际分布。
        acceptance = [item for item in acceptance if item["id"] != "stop_reasons"]
        acceptance.append(
            _acceptance_item(
                "stop_reasons",
                "6 类停止原因在 v1.0 对照侧全部出现（基准覆盖面）",
                len(covered) == len(STOP_REASONS),
                f"{len(covered)}/{len(STOP_REASONS)}：" + ("、".join(covered) or "无"),
            )
        )
        acceptance.append(
            _acceptance_item(
                "v12_improves_without_regression",
                "v1.2 相对 v1.0 不倒退且无 pass→fail 回归",
                not comparison["flips"]["pass_to_fail"]
                and comparison["delta"]["overall"] >= 0,
                f"提升 {comparison['delta']['overall'] * 100:+.1f}pt，"
                f"回归 {len(comparison['flips']['pass_to_fail'])} 个",
            )
        )

        # 三套机制必须真的按臂开关：v1 全关、v1.1 只开收束提示、v1.2 全开。
        flag_expectations = {
            "v1": (False, False, False),
            "v1.1": (True, False, False),
            "v1.2": (True, True, True),
        }
        flag_errors: list[str] = []
        for variant, expected in flag_expectations.items():
            for record in reports[variant]["scenarios"]:
                flags = record["engine_flags"]
                actual = (
                    flags["closing_hint"],
                    flags["overshoot_guard"],
                    flags["stall_strategy"],
                )
                if actual != expected:
                    flag_errors.append(f"{variant}/{record['id']}：{actual}")
        v1_hint_leak = [
            record["id"]
            for record in reports["v1"]["scenarios"]
            if record["closing_hint_rounds"] or record["overshoot_hint_rounds"]
        ]
        acceptance.append(
            _acceptance_item(
                "v12_flags_pinned",
                "三臂开关按预期钉死（v1 全关 / v1.1 只开收束 / v1.2 全开）",
                not flag_errors and not v1_hint_leak,
                "60 个场景开关全对，v1 臂提示注入为 0"
                if not flag_errors and not v1_hint_leak
                else f"开关错 {len(flag_errors)} 处；v1 臂提示泄漏 {v1_hint_leak}",
            )
        )

        envelope_rounds = after_report["summary"]["engine_features"][
            "overshoot_hint_rounds"
        ]
        envelope_scenarios = after_report["summary"]["engine_features"][
            "overshoot_hint_scenarios"
        ]
        acceptance.append(
            _acceptance_item(
                "v12_envelope_hint_delivered",
                "超冲保护把额度提示逐轮送达（非首轮）",
                envelope_rounds > 0,
                f"{envelope_rounds} 轮 / {len(envelope_scenarios)} 个场景："
                + ("、".join(envelope_scenarios) or "无"),
            )
        )

        stall_strategies = [
            record["id"]
            for record in after_report["scenarios"]
            if record["stall_strategies"]
        ]
        acceptance.append(
            _acceptance_item(
                "v12_stall_strategy_switched",
                "停滞重试换策略且给出最少字数",
                len(stall_strategies) > 0
                and all(
                    record["stall_min_characters"]
                    for record in after_report["scenarios"]
                    if record["retries"] > 0
                ),
                f"{len(stall_strategies)} 个场景换过策略："
                + ("、".join(stall_strategies) or "无"),
            )
        )

        baseline_agg = sensitivity["baseline"]
        acceptance.append(
            _acceptance_item(
                "v12_appendix_non_counting",
                "敏感性附录不计入 headline（下界数字与正式运行一致）",
                sensitivity["non_counting"] is True
                and baseline_agg["first_pass"] == after_report["summary"]["first_pass"]
                and baseline_agg["total"] == after_report["summary"]["total"],
                f"下界 {baseline_agg['first_pass']}/{baseline_agg['total']} 与正式一致；"
                f"上界 {sensitivity['obey_with_first_round_envelope']['first_pass']}/"
                f"{sensitivity['obey_with_first_round_envelope']['total']} 标注为非计入",
            )
        )

        acceptance.append(
            _acceptance_item(
                "v12_retry_default_justified",
                "max_stall_retries 默认值有数据依据（多一次重试无收益即不默认）",
                retry["delta_first_pass"] <= 0,
                f"1→2 次重试：通过数 {retry['delta_first_pass']:+d}、"
                f"轮数 {retry['delta_rounds']:+d}、翻转 {len(retry['flips'])} 个，"
                f"默认保持 {after_report['config']['max_stall_retries']}",
            )
        )

        missing = [
            f"{label}={token}"
            for label, token in expected_v12_tokens(comparison, after_report)
            if token not in doc_text
        ]
        acceptance.append(
            _acceptance_item(
                "comparison_doc_numbers",
                "v1.2 对照文档与前后两份 JSON 数字一致",
                not missing,
                f"缺失 {len(missing)} 项" if missing else "全部命中",
            )
        )
        md_missing = [
            f"{label}={token}"
            for label, token in expected_doc_tokens(after_report)
            if token not in md_text
        ]
        acceptance.append(
            _acceptance_item(
                "summary_md_numbers",
                "v1.2 摘要 MD 与 JSON 数字一致",
                not md_missing,
                f"缺失 {len(md_missing)} 项" if md_missing else "全部命中",
            )
        )

        baseline_after = {
            path.name: (_sha256_bytes(path.read_bytes()) if path.exists() else None)
            for path in (DEFAULT_OUT, DEFAULT_MD, DEFAULT_TIMING, DEFAULT_DOC)
        }
        changed = [name for name, digest in baseline_before.items() if baseline_after[name] != digest]
        acceptance.append(
            _acceptance_item(
                "baseline_untouched",
                "v1.0 基线产物未被覆盖",
                not changed,
                "四个产物指纹未变" if not changed else "被改写：" + "、".join(changed),
            )
        )
        after_report["acceptance"] = acceptance

        json_report = {key: value for key, value in after_report.items() if key != "timing"}
        json_report["timing_source"] = V12_TIMING.name
        json_report["comparison_source"] = "budgeted-comparison.json"
        json_report["non_counting_appendix"] = "budgeted-comparison.json#sensitivity"

        comparison_out = HERE / "results" / "budgeted-comparison.json"
        v11_json = {key: value for key, value in v11_report.items() if key != "timing"}
        v11_json["timing_source"] = V11_TIMING.name
        v11_json["comparison_source"] = "budgeted-comparison.json#v1.1"

        for path in (
            V11_OUT,
            V11_MD,
            V11_TIMING,
            V11_DOC,
            V12_OUT,
            V12_MD,
            V12_TIMING,
            V12_DOC,
            comparison_out,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)

        V11_OUT.write_text(
            json.dumps(v11_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        V11_MD.write_text(v11_md, encoding="utf-8")
        V11_TIMING.write_text(
            json.dumps(timings["v1.1"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        V11_DOC.write_text(v11_doc, encoding="utf-8")
        V12_OUT.write_text(
            json.dumps(json_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        V12_MD.write_text(md_text, encoding="utf-8")
        V12_TIMING.write_text(
            json.dumps(timings["v1.2"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # 验收项在文档渲染之后才算出来（要先拿到 doc_text 做数字比对），
        # 因此作为 §12 追加：验收检查针对的是追加前的正文，追加不改变任何数字。
        acceptance_section = [
            "",
            "## 12. 基准验收（实测，逐项可复算）",
            "",
            f"共 **{len(acceptance)} 项**，逐项结果来自 `{V12_OUT.name}` 的 `acceptance` 数组"
            f"（本运行器写入）；结论："
            + ("全部通过" if all(item["passed"] for item in acceptance) else "存在未通过项")
            + "。本节由运行器在验收完成后追加，不参与上面的数字检查。",
            "",
            "| 验收项 | 结果 | 证据 |",
            "| --- | --- | --- |",
        ]
        for item in acceptance:
            acceptance_section.append(
                f"| {item['label']} | {'✅' if item['passed'] else '❌'} | {item['detail']} |"
            )
        doc_text = doc_text + "\n".join(acceptance_section) + "\n"
        V12_DOC.write_text(doc_text, encoding="utf-8")
        comparison_out.write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        if created_temp:
            temp.cleanup()

    before = comparison["before"]
    after = comparison["after"]
    if not quiet:
        print(f"多轮字数预算续写 · v1.0 → v1.2 对照（seed={seed}）")
        print("")
        print(
            _table(
                ["口径", "v1.0", "v1.1", "v1.2", "v1.0→v1.2"],
                [
                    [
                        "全部场景",
                        f"{_pct(before['overall']['rate'])}"
                        f"（{before['overall']['first_pass']}/{before['overall']['total']}）",
                        f"{_pct(comparison['v11']['overall']['rate'])}"
                        f"（{comparison['v11']['overall']['first_pass']}/"
                        f"{comparison['v11']['overall']['total']}）",
                        f"{_pct(after['overall']['rate'])}"
                        f"（{after['overall']['first_pass']}/{after['overall']['total']}）",
                        f"{comparison['delta']['overall'] * 100:+.1f}pt",
                    ],
                    [
                        "3000 字（Q1）",
                        f"{_pct(before['target_3000']['rate'])}"
                        f"（{before['target_3000']['first_pass']}/{before['target_3000']['total']}）",
                        f"{_pct(comparison['v11']['target_3000']['rate'])}"
                        f"（{comparison['v11']['target_3000']['first_pass']}/"
                        f"{comparison['v11']['target_3000']['total']}）",
                        f"{_pct(after['target_3000']['rate'])}"
                        f"（{after['target_3000']['first_pass']}/{after['target_3000']['total']}）",
                        f"{comparison['delta']['target_3000'] * 100:+.1f}pt",
                    ],
                    [
                        "平均轮数",
                        f"{before['rounds_mean']:.2f}",
                        f"{comparison['v11']['rounds_mean']:.2f}",
                        f"{after['rounds_mean']:.2f}",
                        f"{comparison['delta']['rounds_mean']:+.2f}",
                    ],
                    [
                        "超冲轮数 / 停滞换策略",
                        f"0 / 0",
                        f"{comparison['v11']['engine_features']['overshoot_hint_rounds']} / 0",
                        f"{after['engine_features']['overshoot_hint_rounds']} / "
                        f"{len(after['engine_features']['stall_strategies'])}",
                        "提示侧新增能力",
                    ],
                ],
            )
        )
        print("")
        print(
            "翻转：fail→pass "
            f"{comparison['flips']['fail_to_pass'] or '无'}；pass→fail "
            f"{comparison['flips']['pass_to_fail'] or '无'}"
        )
        print("")
        print("非计入的上界（服从式 provider，仅供口径说明）：")
        print(
            f"  首轮额度未合入：{sensitivity['obey_engine_hints']['first_pass']}/"
            f"{sensitivity['obey_engine_hints']['total']}"
            f"（{_pct(sensitivity['obey_engine_hints']['rate'])}）；"
            f"合入首轮额度：{sensitivity['obey_with_first_round_envelope']['first_pass']}/"
            f"{sensitivity['obey_with_first_round_envelope']['total']}"
            f"（{_pct(sensitivity['obey_with_first_round_envelope']['rate'])}）"
        )
        print("")
        print(
            f"Q1 目标（≥{_pct(Q1_TARGET_RATE)}）："
            f"{'达成' if after_report['q1_target_met'] else '未达成'}"
            f"（下界口径）"
        )
        print("")
        print("产物：")
        for path in (
            V11_OUT,
            V11_MD,
            V11_TIMING,
            V11_DOC,
            V12_OUT,
            V12_MD,
            V12_TIMING,
            V12_DOC,
            comparison_out,
        ):
            print(f"  {path}")
        print("")
        print("验收：")
        for item in acceptance:
            mark = "✅" if item["passed"] else "❌"
            print(f"  {mark} {item['label']}：{item['detail']}")
        print("")
        print("v1.1 文档（t15 口径，未改动判定）：")
        for item in v11_acceptance:
            mark = "✅" if item["passed"] else "❌"
            print(f"  {mark} {item['label']}：{item['detail']}")

    all_passed = all(item["passed"] for item in acceptance) and all(
        item["passed"] for item in v11_acceptance
    )
    if not quiet:
        print("")
        print(f"verdict：{'PASS' if all_passed else 'FAIL'}")
    return 0 if all_passed else 1

# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def build_report(
    seed: int,
    repeat: int,
    work_dir: Path,
    variant: str = "v1",
) -> tuple[dict, dict]:
    """跑 repeat 次套件，返回 (报告, 耗时观测)。"""
    engine = ENGINE_VARIANTS[variant]
    records, timings = run_suite(seed, work_dir, variant)
    first_payload = _canonical_payload(
        {
            "config": {
                "seed": seed,
                "repeat": repeat,
                "scenarios": len(SCENARIO_TABLE),
                "targets": sorted({target for _, target, _ in SCENARIO_TABLE}),
                "max_rounds": DEFAULT_MAX_ROUNDS,
                "min_round_gain": DEFAULT_MIN_ROUND_GAIN,
                "tolerance": TARGET_TOLERANCE,
                "counting_rule": COUNTING_RULE,
                "q1_target_characters": Q1_TARGET_CHARACTERS,
                "q1_target_rate": Q1_TARGET_RATE,
                "engine_variant": variant,
                "max_stall_retries": engine["max_stall_retries"],
                "hard_max_rounds": engine["hard_max_rounds"],
                # v1.2：三套机制必须显式记账，否则「哪一臂开了什么」只能靠读代码。
                "closing_hint": engine["closing_hint"],
                "overshoot_guard": engine["overshoot_guard"],
                "stall_strategy": engine["stall_strategy"],
            },
            "scenarios": records,
            "summary": summarize(records),
        }
    )
    digest = digest_of(first_payload)

    identical = True
    for _ in range(repeat - 1):
        repeated_records, _ = run_suite(seed, work_dir, variant)
        repeated_payload = _canonical_payload(
            {
                "config": first_payload["config"],
                "scenarios": repeated_records,
                "summary": summarize(repeated_records),
            }
        )
        if digest_of(repeated_payload) != digest:
            identical = False

    summary = summarize(records)
    q1_row = summary["by_target"][str(Q1_TARGET_CHARACTERS)]

    report = {
        "benchmark": BENCHMARK_NAME,
        "version": BENCHMARK_VERSION,
        "baseline_date": BASELINE_DATE,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "engine_module": "backend/moyu_continuation.py",
            "engine_sha256": _sha256_bytes(
                (ROOT / "backend" / "moyu_continuation.py").read_bytes()
            ),
        },
        "config": first_payload["config"],
        "engine": {
            "variant": variant,
            "label": engine["label"],
            "engine_version": engine["engine_version"],
            "max_stall_retries": engine["max_stall_retries"],
            "hard_max_rounds": engine["hard_max_rounds"],
            "closing_hint": engine["closing_hint"],
            "overshoot_guard": engine["overshoot_guard"],
            "stall_strategy": engine["stall_strategy"],
            "hard_max_rounds_note": (
                "None 表示取各场景的 max_rounds，即禁用弹性轮数"
                if engine["hard_max_rounds"] is None
                else "固定硬上限"
            ),
        },
        "assumptions": {
            "mix_is_assumption": True,
            "mix_note": (
                "20 个场景的档位配比是假设，不是从真实模型测得的分布；"
                "总体一次通过率随配比变化，分层包络更可信。"
            ),
            "qualified_tiers": list(QUALIFIED_TIERS),
            "deficient_tiers": list(DEFICIENT_TIERS),
            "interrupted_tiers": list(INTERRUPTED_TIERS),
            "tiers": {
                tier: {
                    "label": TIERS[tier]["label"],
                    "yield_note": TIERS[tier]["yield_note"],
                    "behaviour": TIERS[tier]["behaviour"],
                    "ratio": TIERS[tier].get("ratio"),
                    "fixed_yield": TIERS[tier].get("fixed_yield"),
                }
                for tier in TIER_ORDER
            },
        },
        "scenarios": records,
        "summary": summary,
        "q1_target_met": q1_row["rate"] >= Q1_TARGET_RATE,
        "determinism": {
            "repeat": repeat,
            "identical": identical,
            "digest": digest,
        },
        "timing": summarize_timing(timings),
    }
    return report, report["timing"]


def run(
    seed: int,
    repeat: int,
    out_json: Path,
    out_md: Path,
    out_timing: Path,
    out_doc: Path,
    work_dir: Path | None,
    quiet: bool,
) -> int:
    if repeat < 1:
        print("错误：--repeat 必须 >= 1。", file=sys.stderr)
        return 2
    if seed < 0:
        print("错误：--seed 必须 >= 0。", file=sys.stderr)
        return 2

    created_temp = False
    if work_dir is None:
        temp = tempfile.TemporaryDirectory(prefix="moyu-budgeted-")
        created_temp = True
        active_work = Path(temp.name)
    else:
        active_work = work_dir
        active_work.mkdir(parents=True, exist_ok=True)

    try:
        report, timing = build_report(seed, repeat, active_work)
        doc_text = render_doc(report)
        md_text = render_summary(report)
        report["acceptance"] = evaluate_acceptance(report, doc_text, md_text, timing)

        # 墙钟耗时是观测值，写进基线 JSON 会让同一 seed 的两次运行不再逐字节一致。
        # 因此基线 JSON 只保留确定性核心，耗时单独落盘并在 JSON 里留下引用。
        json_report = {key: value for key, value in report.items() if key != "timing"}
        json_report["timing_source"] = out_timing.name

        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_timing.parent.mkdir(parents=True, exist_ok=True)
        out_doc.parent.mkdir(parents=True, exist_ok=True)

        out_json.write_text(
            json.dumps(json_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        out_md.write_text(md_text, encoding="utf-8")
        out_timing.write_text(
            json.dumps(timing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        out_doc.write_text(doc_text, encoding="utf-8")
    finally:
        if created_temp:
            temp.cleanup()

    summary = report["summary"]
    if not quiet:
        print(f"多轮字数预算续写 · 一次通过率基准（seed={seed}）")
        print(f"{'场景':<7}{'目标':>6}  {'档位':<14}{'轮数':>5}{'字数':>7}  {'停止原因':<18}一次通过")
        for record in report["scenarios"]:
            print(
                f"{record['id']:<7}{record['target_characters']:>6}  "
                f"{record['tier']:<14}{record['rounds']:>5}{record['characters']:>7}  "
                f"{record['stop_reason']:<18}"
                f"{'是' if record['first_pass'] else '否'}"
            )
        print("")
        print(
            f"一次通过率（全部）：{_pct(summary['rate'])}"
            f"（{summary['first_pass']}/{summary['total']}）"
        )
        q1 = summary["by_target"][str(Q1_TARGET_CHARACTERS)]
        print(
            f"一次通过率（3000 字，Q1 口径）：{_pct(q1['rate'])}"
            f"（{q1['first_pass']}/{q1['total']}）"
        )
        print(
            f"一次通过率（生成侧合格档）：{_pct(summary['qualified']['rate'])}"
            f"（{summary['qualified']['first_pass']}/{summary['qualified']['total']}）"
        )
        print(f"平均轮数：{summary['rounds']['mean']:.2f}")
        print("停止原因分布：" + "，".join(
            f"{reason}={summary['stop_reasons'][reason]}" for reason in STOP_REASONS
        ))
        print(
            f"正文不变性：{summary['body_invariance']['unchanged']}"
            f"/{summary['body_invariance']['checked']} 哈希一致"
        )
        print(f"确定性：repeat={report['determinism']['repeat']}，"
              f"一致={'是' if report['determinism']['identical'] else '否'}，"
              f"digest={report['determinism']['digest']}")
        print("")
        print(f"Q1 目标（≥{_pct(Q1_TARGET_RATE)}）："
              f"{'达成' if report['q1_target_met'] else '未达成'}")
        print("")
        print("产物：")
        print(f"  {out_json}")
        print(f"  {out_md}")
        print(f"  {out_timing}")
        print(f"  {out_doc}")
        print("")
        print("验收：")
        for item in report["acceptance"]:
            mark = "✅" if item["passed"] else "❌"
            print(f"  {mark} {item['label']}：{item['detail']}")

    all_passed = all(item["passed"] for item in report["acceptance"])
    if not quiet:
        print("")
        print(f"verdict：{'PASS' if all_passed else 'FAIL'}")
    return 0 if all_passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Q1-5b：多轮字数预算续写的一次通过率基准（fake provider，不调用真实模型）"
    )
    parser.add_argument("--seed", type=int, default=20261023, help="随机种子，默认 20261023")
    parser.add_argument("--repeat", type=int, default=2, help="重复运行次数，用于验证确定性，默认 2")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSON 报告输出路径")
    parser.add_argument("--md", default=str(DEFAULT_MD), help="Markdown 摘要输出路径")
    parser.add_argument("--timing", default=str(DEFAULT_TIMING), help="耗时观测输出路径")
    parser.add_argument("--doc", default=str(DEFAULT_DOC), help="正式基线文档输出路径")
    parser.add_argument("--work-dir", default=None, help="正文快照目录；缺省用系统临时目录并在结束时清理")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="跑 v1.0 锁定语义与 v1.1 语义各一遍，写出 v1.1 基线与前后对照（t15）",
    )
    parser.add_argument("--quiet", action="store_true", help="只输出退出码")
    args = parser.parse_args(argv)

    if args.compare:
        return run_comparison(
            seed=args.seed,
            repeat=args.repeat,
            work_dir=Path(args.work_dir) if args.work_dir else None,
            quiet=args.quiet,
        )

    return run(
        seed=args.seed,
        repeat=args.repeat,
        out_json=Path(args.out),
        out_md=Path(args.md),
        out_timing=Path(args.timing),
        out_doc=Path(args.doc),
        work_dir=Path(args.work_dir) if args.work_dir else None,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    raise SystemExit(main())
