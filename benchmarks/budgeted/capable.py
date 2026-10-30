#!/usr/bin/env python3
"""Q1 capable-provider arm.

The legacy Q1 number uses an adversarial fake provider that ignores every
length hint.  That is a useful robustness lower bound, but it cannot answer a
capability question: a provider that writes only 12-30 characters per round
cannot be made to deliver a 3000-character chapter by any engine without
spending an unbounded number of calls.

This arm therefore measures the engine under one explicit, predeclared
assumption: the provider obeys the engine's per-round ``max_characters`` and
``min_characters`` hints, and the caller supplies the first-round envelope with
``round_budget(0, target)``.  External interruptions (budget exhaustion and
author cancellation) are reported but excluded from the Q1 engine denominator,
because the engine must not bypass them.

It is a *different contract* from the legacy 7/9 adversarial number.  Both are
kept; neither overwrites the other.

Run::

    .venv/bin/python benchmarks/budgeted/capable.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.budgeted.run import (  # noqa: E402
    INTERRUPTED_TIERS,
    Q1_TARGET_CHARACTERS,
    ObeyingProvider,
    run_suite,
    summarize,
)

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "results" / "budgeted-capable.json"
DEFAULT_MD = HERE / "results" / "budgeted-capable.md"
DEFAULT_LEGACY = HERE / "results" / "budgeted-v1.2-baseline.json"
Q1_ENGINE_TARGET_RATE = 0.90


def _provider_factory(tier: str, target: int, rng: Any) -> ObeyingProvider:
    return ObeyingProvider(
        tier,
        target,
        rng,
        obey_first_round_envelope=True,
    )


def _group(records: list[dict], target: int | None = None, exclude_interrupted: bool = False) -> dict:
    rows = records
    if target is not None:
        rows = [row for row in rows if row["target_characters"] == target]
    if exclude_interrupted:
        rows = [row for row in rows if row["tier"] not in INTERRUPTED_TIERS]
    total = len(rows)
    passed = sum(1 for row in rows if row["first_pass"])
    return {
        "total": total,
        "passed": passed,
        "rate": round(passed / total, 4) if total else 0.0,
        "ids": [row["id"] for row in rows],
        "failed_ids": [row["id"] for row in rows if not row["first_pass"]],
    }


def _legacy_adversarial(path: Path) -> dict:
    if not path.exists():
        return {"available": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    target = payload["summary"]["by_target"][str(Q1_TARGET_CHARACTERS)]
    return {
        "available": True,
        "source": str(path.relative_to(ROOT)),
        "target_3000": target,
        "q1_target_met": payload.get("q1_target_met"),
        "engine_digest": payload.get("engine", {}).get("digest"),
    }


def _scenario(records: list[dict], scenario_id: str) -> dict:
    for row in records:
        if row["id"] == scenario_id:
            return {
                "id": row["id"],
                "tier": row["tier"],
                "target_characters": row["target_characters"],
                "first_pass": row["first_pass"],
                "stop_reason": row["stop_reason"],
                "characters": row["characters"],
                "rounds": row["rounds"],
                "target_range": row["target_range"],
                "overshoot": row["overshoot"],
                "shortfall": row["shortfall"],
            }
    raise AssertionError(f"scenario not found: {scenario_id}")


def build_report(seed: int = 20261023) -> tuple[dict, str]:
    with tempfile.TemporaryDirectory(prefix="moyu-q1-capable-") as temp:
        records, _ = run_suite(
            seed,
            Path(temp),
            "v1.2",
            provider_factory=_provider_factory,
        )

    summary = summarize(records)
    all_targets = _group(records)
    capable_all_3000 = _group(records, Q1_TARGET_CHARACTERS)
    engine_3000 = _group(records, Q1_TARGET_CHARACTERS, exclude_interrupted=True)
    q1_engine_met = engine_3000["rate"] >= Q1_ENGINE_TARGET_RATE
    legacy = _legacy_adversarial(DEFAULT_LEGACY)
    failures = [
        {
            "id": row["id"],
            "tier": row["tier"],
            "stop_reason": row["stop_reason"],
            "characters": row["characters"],
            "target_range": row["target_range"],
        }
        for row in records
        if not row["first_pass"]
    ]
    scenarios = [
        {
            "id": row["id"],
            "tier": row["tier"],
            "target_characters": row["target_characters"],
            "first_pass": row["first_pass"],
            "stop_reason": row["stop_reason"],
            "characters": row["characters"],
            "rounds": row["rounds"],
            "target_range": row["target_range"],
            "overshoot": row["overshoot"],
            "shortfall": row["shortfall"],
            "obeyed_rounds": row["obeyed_rounds"],
        }
        for row in records
    ]
    payload = {
        "benchmark": "q1-capable-continuation",
        "version": "1.0",
        "seed": seed,
        "engine_variant": "v1.2",
        "provider": "obeying_first_round_envelope",
        "scope": (
            "capable provider: obeys max_characters/min_characters and receives "
            "the first-round envelope from the caller; external interruptions "
            "are reported but excluded from q1_engine"
        ),
        "definitions": {
            "q1_engine": (
                "3000-character first-pass rate on scenarios whose tier is not "
                "an external interruption (budget_limited/canceled)"
            ),
            "q1_capable_all": (
                "3000-character first-pass rate on every capable-provider "
                "scenario, including external interruptions"
            ),
            "legacy_adversarial": (
                "the existing fake-provider headline; it ignores all length "
                "hints and remains a robustness lower bound, not this arm"
            ),
        },
        "q1_engine": {
            **engine_3000,
            "target_rate": Q1_ENGINE_TARGET_RATE,
            "met": q1_engine_met,
        },
        "q1_capable_all": {
            **capable_all_3000,
            "target_rate": Q1_ENGINE_TARGET_RATE,
            "met": capable_all_3000["rate"] >= Q1_ENGINE_TARGET_RATE,
        },
        "all_targets": {
            **all_targets,
            "rate": summary["rate"],
        },
        "legacy_adversarial": legacy,
        "sc15": _scenario(records, "sc-15"),
        "sc18": _scenario(records, "sc-18"),
        "sc19": _scenario(records, "sc-19"),
        "sc20": _scenario(records, "sc-20"),
        "failures": failures,
        "scenarios": scenarios,
        "summary": summary,
    }
    digest_input = {
        "seed": seed,
        "q1_engine": payload["q1_engine"],
        "q1_capable_all": payload["q1_capable_all"],
        "all_targets": payload["all_targets"],
        "scenarios": scenarios,
    }
    digest = hashlib.sha256(
        json.dumps(digest_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    payload["digest"] = f"sha256:{digest}"

    sc15 = _scenario(records, "sc-15")
    sc18 = _scenario(records, "sc-18")
    sc19 = _scenario(records, "sc-19")
    lines = [
        "# Q1 capable-provider arm",
        "",
        f"> seed={seed}；provider 服从 `max_characters` / `min_characters`，"
        "调用方把首轮额度合入开场提示。",
        "",
        "## 结论",
        "",
        "| 口径 | 通过 | 总数 | 比率 | 判定 |",
        "| --- | ---: | ---: | ---: | --- |",
        f"| Q1 engine（3000 字，排除外部中断） | {engine_3000['passed']} | "
        f"{engine_3000['total']} | {engine_3000['rate'] * 100:.1f}% | "
        f"{'达成' if q1_engine_met else '未达成'} |",
        f"| Q1 capable all（3000 字，含外部中断） | {capable_all_3000['passed']} | "
        f"{capable_all_3000['total']} | {capable_all_3000['rate'] * 100:.1f}% | "
        f"{'达成' if capable_all_3000['rate'] >= Q1_ENGINE_TARGET_RATE else '未达成'} |",
        f"| 全部目标（含外部中断） | {all_targets['passed']} | {all_targets['total']} | "
        f"{summary['rate'] * 100:.1f}% | — |",
        "",
        "## 关键场景",
        "",
        f"- sc-15（4000 / volatile）：**{sc15['first_pass'] and 'PASS' or 'FAIL'}**，"
        f"{sc15['characters']} 字 / {sc15['rounds']} 轮 / `{sc15['stop_reason']}`。",
        f"- sc-18（3000 / stalling）：**{sc18['first_pass'] and 'PASS' or 'FAIL'}**，"
        f"{sc18['characters']} 字 / {sc18['rounds']} 轮 / `{sc18['stop_reason']}`。",
        f"- sc-19（3000 / budget_limited）：**{sc19['first_pass'] and 'PASS' or 'FAIL'}**，"
        f"{sc19['characters']} 字 / `{sc19['stop_reason']}`；外部预算中断，不计入 Q1 engine。",
        "",
        "## 不能混用的旧口径",
        "",
        f"- legacy adversarial 3000：{legacy.get('target_3000', {}).get('first_pass', '?')}/"
        f"{legacy.get('target_3000', {}).get('total', '?')}"
        f"（{legacy.get('target_3000', {}).get('rate', 0) * 100:.1f}%），"
        f"`q1_target_met={legacy.get('q1_target_met')}`；它是“模型不服从提示”的下界，不是本臂。",
        "- 本臂不修改旧 baseline、不改变旧结论，也不把 `q1_engine` 的数字写成 legacy Q1。",
        "",
        f"确定性摘要：`{payload['digest']}`",
        "",
    ]
    return payload, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Q1 capable-provider arm")
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--md", default=str(DEFAULT_MD))
    args = parser.parse_args(argv)

    payload, markdown = build_report(seed=args.seed)
    out = Path(args.out)
    md = Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md.write_text(markdown, encoding="utf-8")
    print(
        f"q1_engine={payload['q1_engine']['passed']}/{payload['q1_engine']['total']} "
        f"({payload['q1_engine']['rate']:.1%}) "
        f"q1_capable_all={payload['q1_capable_all']['passed']}/"
        f"{payload['q1_capable_all']['total']} "
        f"all={payload['all_targets']['passed']}/{payload['all_targets']['total']} "
        f"digest={payload['digest'][:19]}…"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
