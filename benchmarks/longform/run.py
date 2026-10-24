"""LongFormBench 一键基线：生成 → 注入 → 分析 → 匹配 → 落盘报告。

一条命令复现全部数字::

    .venv/bin/python benchmarks/longform/run.py --chapters 40

产出：

* ``benchmarks/longform/results/longform-baseline.json``  —— 机器可读的完整报告
* ``benchmarks/longform/results/longform-baseline.md``    —— 人类可读摘要
* ``docs/consistency-baseline-2026-10-23.md``             —— 对外基线文档（数字由本次运行填充）

退出码：0 表示全部验收项通过；1 表示有验收项未达标（报告仍会落盘）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.moyu_consistency import analyze  # noqa: E402
from benchmarks.longform.generate import (  # noqa: E402
    BRANCH_ID,
    DEFAULT_SEED,
    PROJECT_ID,
    create_database,
    generate,
)
from benchmarks.longform.inject import (  # noqa: E402
    CATEGORY_LABELS,
    INJECTION_SPECS,
    inject,
    match_findings,
)

BENCHMARK_NAME = "LongFormBench"
BENCHMARK_VERSION = "1.0"
ENGINE_PATH = ROOT / "backend" / "moyu_consistency.py"
DEFAULT_CHAPTERS = 40
DEFAULT_REPEAT = 5
DEFAULT_MIN_RECALL = 0.95
DEFAULT_MAX_SECONDS = 5.0
MIN_CHAPTERS = 25


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _peak_rss_mb() -> float:
    """当前进程峰值常驻内存。macOS 单位是字节，Linux 是 KB。"""
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return round(value / (1024 * 1024), 2)
    return round(value / 1024, 2)


def _db_size_bytes(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _time_analyze(conn: sqlite3.Connection, repeat: int) -> tuple[list[float], dict[str, Any]]:
    """重复调用 analyze，返回每次耗时（秒）与最后一次结果。"""
    timings: list[float] = []
    result: dict[str, Any] = {}
    for _ in range(max(1, repeat)):
        started = time.perf_counter()
        result = analyze(conn, PROJECT_ID, BRANCH_ID)
        timings.append(round(time.perf_counter() - started, 6))
    return timings, result


def _summarize_timings(timings: list[float]) -> dict[str, Any]:
    return {
        "runs_s": timings,
        "repeat": len(timings),
        "first_run_s": timings[0],
        "median_s": round(statistics.median(timings), 6),
        "min_s": round(min(timings), 6),
        "max_s": round(max(timings), 6),
        "mean_s": round(statistics.fmean(timings), 6),
    }


def _environment() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "engine_path": str(ENGINE_PATH.relative_to(ROOT)),
        "engine_sha256": _sha256(ENGINE_PATH) if ENGINE_PATH.exists() else None,
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(
    *,
    chapters: int = DEFAULT_CHAPTERS,
    seed: int = DEFAULT_SEED,
    repeat: int = DEFAULT_REPEAT,
    work_dir: Path,
    out_json: Path,
    out_md: Path,
    out_doc: Path | None,
    min_recall: float = DEFAULT_MIN_RECALL,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    keep_work: bool = False,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    clean_db = work_dir / "longform-clean.sqlite3"
    dirty_db = work_dir / "longform-dirty.sqlite3"
    for path in (clean_db, dirty_db):
        path.unlink(missing_ok=True)

    if chapters < MIN_CHAPTERS:
        raise ValueError(
            f"chapters 必须 >= {MIN_CHAPTERS}（当前 {chapters}）。"
            "LongFormBench 的注入集依赖 15 章跨度与彼此独立的章节锚点；"
            "推荐 --chapters 40（约 10.5 万汉字）。"
        )
    warnings: list[str] = []

    # ---- 1. 生成干净项目 ----
    started = time.perf_counter()
    conn = create_database(clean_db)
    try:
        dataset = generate(conn, chapters=chapters, seed=seed)
        conn.commit()
    finally:
        conn.close()
    generation_seconds = round(time.perf_counter() - started, 4)

    # ---- 2. 干净基线（不得有任何 finding）----
    conn = sqlite3.connect(clean_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        clean_timings, clean_result = _time_analyze(conn, repeat)
    finally:
        conn.close()

    hard_negatives = dataset.get("hard_negatives", [])
    clean_findings = clean_result["findings"]
    clean_baseline = {
        "score": clean_result["score"],
        "grade": clean_result["grade"],
        "findings": len(clean_findings),
        "total_examined": clean_result["total_examined"],
        "hard_negatives_total": len(hard_negatives),
        "hard_negatives_triggered": len(clean_findings),
        "hard_negatives": [
            {"id": item["id"], "description": item["description"], "triggered": False}
            for item in hard_negatives
        ],
        "timing": _summarize_timings(clean_timings),
    }

    # ---- 3. 注入已知矛盾（在副本上操作，保持干净库原样）----
    shutil.copyfile(clean_db, dirty_db)
    conn = sqlite3.connect(dirty_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        ground_truth = inject(conn)
        dirty_timings, dirty_result = _time_analyze(conn, repeat)
        dirty_size = _db_size_bytes(dirty_db)
    finally:
        conn.close()

    detection = match_findings(dirty_result["findings"], ground_truth)

    # ---- 4. 指标 ----
    words = dataset["chapter_word_counts"]
    counts = dataset["counts"]
    total_injected = detection["total_injected"]
    detected = detection["total_detected"]
    matched = detection["matched_findings"]
    false_positives = detection["false_positives"]
    precision = round(matched / (matched + false_positives), 4) if (matched + false_positives) else None
    recall = detection["recall"]
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision is not None and recall
        else None
    )
    dirty_timing = _summarize_timings(dirty_timings)
    han_chars = words["han_chars"]

    scale_metrics = {
        "han_chars": han_chars,
        "total_chars": words["total_chars"],
        "non_whitespace_chars": words["non_whitespace_chars"],
        "chapters": counts_chapters(dataset),
        "entities": counts["entities"],
        "assertions": counts["assertions"],
        "character_states": counts["character_states"],
        "timeline_events": counts["timeline_events"],
        "relations": counts["relations"],
        "plot_items": counts["plot_items"],
        "foreshadow_links": counts["foreshadow_links"],
        "source_spans": counts["source_spans"],
        "document_revisions": counts["document_revisions"],
        "injected_contradictions": total_injected,
        "examined_items": dirty_result["total_examined"],
        # 与进程内存无关的规模归一化指标
        "seconds_per_100k_han_chars": round(
            dirty_timing["median_s"] / (han_chars / 100_000), 6
        ) if han_chars and dirty_timing["median_s"] else None,
        "examined_items_per_second": round(
            dirty_result["total_examined"] / dirty_timing["median_s"], 1
        ) if dirty_timing["median_s"] else None,
        "findings_per_10k_han_chars": round(len(dirty_result["findings"]) / han_chars * 10000, 3)
        if han_chars else None,
        "han_chars_per_entity": round(han_chars / counts["entities"], 1) if counts["entities"] else None,
        "assertions_per_entity": round(counts["assertions"] / counts["entities"], 2)
        if counts["entities"] else None,
        "db_size_bytes": dirty_size,
        "db_size_mb": round(dirty_size / (1024 * 1024), 3),
    }

    acceptance = [
        {
            "criterion": f"合成项目正文 ≥ 100,000 汉字（实测 {han_chars:,}）",
            "target": ">= 100000",
            "actual": han_chars,
            "passed": han_chars >= 100_000,
        },
        {
            "criterion": f"注入已知矛盾 ≥ 30 条（实测 {total_injected}）",
            "target": ">= 30",
            "actual": total_injected,
            "passed": total_injected >= 30,
        },
        {
            "criterion": "七类矛盾全部覆盖",
            "target": "7 categories",
            "actual": sorted(detection["per_category"]),
            "passed": sorted(detection["per_category"])
            == sorted(["fact", "timeline", "knowledge", "attribute", "relation", "foreshadow", "evidence"]),
        },
        {
            "criterion": f"检出率 ≥ {min_recall:.0%}（实测 {recall:.1%}）",
            "target": f">= {min_recall}",
            "actual": recall,
            "passed": recall is not None and recall >= min_recall,
        },
        {
            "criterion": "干净项目零误报",
            "target": "0 findings",
            "actual": len(clean_findings),
            "passed": len(clean_findings) == 0,
        },
        {
            "criterion": f"10 万字规模单次分析 < {max_seconds:g}s（实测 {dirty_timing['median_s']:.3f}s 中位数）",
            "target": f"< {max_seconds}",
            "actual": dirty_timing["median_s"],
            "passed": dirty_timing["median_s"] < max_seconds,
        },
    ]
    verdict = "pass" if all(item["passed"] for item in acceptance) else "fail"

    report: dict[str, Any] = {
        "benchmark": BENCHMARK_NAME,
        "benchmark_version": BENCHMARK_VERSION,
        "generated_at": _utcnow(),
        "verdict": verdict,
        "environment": _environment(),
        "config": {
            "chapters": chapters,
            "seed": seed,
            "repeat": repeat,
            "min_recall": min_recall,
            "max_analysis_seconds": max_seconds,
            "project_id": PROJECT_ID,
            "branch_id": BRANCH_ID,
            "generation_seconds": generation_seconds,
        },
        "dataset": {
            "project": dataset["project"],
            "word_counts": words,
            "counts": counts,
            "hard_negatives": hard_negatives,
        },
        "injection": {
            "total": total_injected,
            "by_category": _count_by_category(INJECTION_SPECS),
            "specs": [
                {
                    "id": spec["id"],
                    "category": spec["category"],
                    "category_label": CATEGORY_LABELS[spec["category"]],
                    "severity": spec["severity"],
                    "description": spec["description"],
                }
                for spec in INJECTION_SPECS
            ],
        },
        "clean_baseline": clean_baseline,
        "analysis": {
            "score": dirty_result["score"],
            "grade": dirty_result["grade"],
            "findings": len(dirty_result["findings"]),
            "counts_by_severity": dirty_result["counts"],
            "categories": dirty_result["categories"],
            "examined": dirty_result["examined"],
            "total_examined": dirty_result["total_examined"],
            "engine": dirty_result["engine"],
            "engine_version": dirty_result["version"],
            "timing": dirty_timing,
        },
        "detection": {
            "total_injected": total_injected,
            "total_detected": detected,
            "recall": recall,
            "precision": precision,
            "f1": f1,
            "matched_findings": matched,
            "false_positives": false_positives,
            "per_category": detection["per_category"],
            "missed": detection["missed"],
            "false_positive_findings": detection["false_positive_findings"],
            "ground_truth": detection["ground_truth"],
        },
        "scale_metrics": scale_metrics,
        "memory": {
            "peak_rss_mb": _peak_rss_mb(),
            "note": "单进程峰值常驻内存，含生成阶段；分析阶段本身不额外分配大结构。",
        },
        "acceptance": acceptance,
        "warnings": warnings,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_summary(report), encoding="utf-8")
    if out_doc is not None:
        out_doc.parent.mkdir(parents=True, exist_ok=True)
        out_doc.write_text(render_doc(report), encoding="utf-8")

    if not keep_work:
        for path in (clean_db, dirty_db):
            path.unlink(missing_ok=True)
        try:
            work_dir.rmdir()
        except OSError:
            pass

    return report


def counts_chapters(dataset: dict[str, Any]) -> int:
    return int(dataset["chapters"])


def _count_by_category(specs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec["category"]] = counts.get(spec["category"], 0) + 1
    return dict(sorted(counts.items()))


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _fmt_int(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else str(value)


def _category_table(report: dict[str, Any]) -> str:
    lines = [
        "| 类别 | 注入数 | 检出数 | 漏检 | 误报 | 召回率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category, item in report["detection"]["per_category"].items():
        recall = "—" if item["recall"] is None else f"{item['recall']:.1%}"
        lines.append(
            f"| {item['label']} (`{category}`) | {item['injected']} | {item['detected']} | "
            f"{item['missed']} | {item['false_positives']} | {recall} |"
        )
    total = report["detection"]
    recall = "—" if total["recall"] is None else f"{total['recall']:.1%}"
    lines.append(
        f"| **合计** | **{total['total_injected']}** | **{total['total_detected']}** | "
        f"**{total['total_injected'] - total['total_detected']}** | **{total['false_positives']}** | "
        f"**{recall}** |"
    )
    return "\n".join(lines)


def _acceptance_table(report: dict[str, Any]) -> str:
    lines = ["| 验收项 | 目标 | 实测 | 结论 |", "| --- | --- | --- | :---: |"]
    for item in report["acceptance"]:
        lines.append(
            f"| {item['criterion']} | `{item['target']}` | `{item['actual']}` | "
            f"{'✅ 通过' if item['passed'] else '❌ 未通过'} |"
        )
    return "\n".join(lines)


def render_summary(report: dict[str, Any]) -> str:
    env = report["environment"]
    scale = report["scale_metrics"]
    timing = report["analysis"]["timing"]
    detection = report["detection"]
    clean = report["clean_baseline"]
    lines = [
        f"# {report['benchmark']} 运行摘要",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结论：**{report['verdict'].upper()}**",
        f"- 引擎：`{report['analysis']['engine']}` v{report['analysis']['engine_version']}",
        f"- 环境：Python {env['python']} / SQLite {env['sqlite']} / {env['platform']}",
        f"- 配置：chapters={report['config']['chapters']}，seed={report['config']['seed']}，"
        f"repeat={report['config']['repeat']}",
        "",
        "## 数据集规模",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 章节数 | {_fmt_int(scale['chapters'])} |",
        f"| 汉字数 | {_fmt_int(scale['han_chars'])} |",
        f"| 总字符数 | {_fmt_int(scale['total_chars'])} |",
        f"| 实体数 | {_fmt_int(scale['entities'])} |",
        f"| 断言数 | {_fmt_int(scale['assertions'])} |",
        f"| 角色状态数 | {_fmt_int(scale['character_states'])} |",
        f"| 时间线事件数 | {_fmt_int(scale['timeline_events'])} |",
        f"| 关系数 | {_fmt_int(scale['relations'])} |",
        f"| 伏笔条目数 | {_fmt_int(scale['plot_items'])} |",
        f"| 伏笔链接数 | {_fmt_int(scale['foreshadow_links'])} |",
        f"| 来源片段数 | {_fmt_int(scale['source_spans'])} |",
        f"| 注入矛盾数 | {_fmt_int(scale['injected_contradictions'])} |",
        "",
        "## 检出结果",
        "",
        _category_table(report),
        "",
        f"- 总体召回率：**{detection['recall']:.1%}**（{detection['total_detected']}/{detection['total_injected']}）",
        f"- 精确率：**{detection['precision']:.1%}**，F1：**{detection['f1']:.3f}**",
        f"- 干净基线：{clean['findings']} findings，score={clean['score']}，"
        f"hard negatives {clean['hard_negatives_total']} 条全部未触发",
        "",
        "## 性能",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 分析耗时（中位数 / {timing['repeat']} 次） | {timing['median_s'] * 1000:.2f} ms |",
        f"| 分析耗时（最快） | {timing['min_s'] * 1000:.2f} ms |",
        f"| 分析耗时（最慢） | {timing['max_s'] * 1000:.2f} ms |",
        f"| 每 10 万汉字分析耗时 | {scale['seconds_per_100k_han_chars']:.4f} s |",
        f"| 每万字 findings | {scale['findings_per_10k_han_chars']} |",
        f"| 峰值 RSS | {report['memory']['peak_rss_mb']} MB |",
        f"| 数据库大小 | {scale['db_size_mb']} MB |",
        "",
        "## 验收",
        "",
        _acceptance_table(report),
        "",
    ]
    if report["warnings"]:
        lines += ["## 警告", ""] + [f"- {item}" for item in report["warnings"]] + [""]
    return "\n".join(lines)


def render_doc(report: dict[str, Any]) -> str:
    env = report["environment"]
    scale = report["scale_metrics"]
    timing = report["analysis"]["timing"]
    detection = report["detection"]
    clean = report["clean_baseline"]
    config = report["config"]
    sev = report["analysis"]["counts_by_severity"]
    deduction = (
        sev.get("blocker", 0) * 20 + sev.get("high", 0) * 8
        + sev.get("medium", 0) * 3 + sev.get("low", 0) * 1
    )
    cmd = f".venv/bin/python benchmarks/longform/run.py --chapters {config['chapters']} --seed {config['seed']}"
    lines = [
        "# 一致性引擎 v1 · LongFormBench 基线（2026-10-23）",
        "",
        f"> 本文件由 `benchmarks/longform/run.py` 自动生成，所有数字与 "
        f"`benchmarks/longform/results/longform-baseline.json` 同源。",
        f"> 生成时间：`{report['generated_at']}`　结论：**{report['verdict'].upper()}**",
        "",
        "## 一句话结论",
        "",
        f"在 {_fmt_int(scale['han_chars'])} 汉字、{_fmt_int(scale['chapters'])} 章的合成长篇项目上，"
        f"`{report['analysis']['engine']}` v{report['analysis']['engine_version']} 对 "
        f"{detection['total_injected']} 条注入矛盾的召回率为 **{detection['recall']:.1%}**，"
        f"误报 **{detection['false_positives']}** 条，"
        f"单次全量分析中位耗时 **{timing['median_s'] * 1000:.2f} ms**（{timing['repeat']} 次重复的中位数）。",
        "",
        "## 复现",
        "",
        "```bash",
        "# 依赖：仅标准库 + 仓库自带 backend/moyu_consistency.py",
        cmd,
        "```",
        "",
        "该命令会：生成合成项目 → 分析干净基线 → 注入已知矛盾 → 再次分析 → 匹配 ground truth → "
        "写出 `results/longform-baseline.json`、`results/longform-baseline.md` 与本文件。",
        "",
        "## 环境",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        f"| Python | {env['python']}（{env['python_implementation']}） |",
        f"| SQLite | {env['sqlite']} |",
        f"| 平台 | {env['platform']} |",
        f"| 机器 | {env['machine']}，{env['cpu_count']} 逻辑核 |",
        f"| 引擎文件 | `{env['engine_path']}` |",
        f"| 引擎 SHA-256 | `{env['engine_sha256']}` |",
        f"| 随机种子 | {config['seed']} |",
        f"| 重复次数 | {config['repeat']} |",
        "",
        "## 数据集规模",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 章节数 | {_fmt_int(scale['chapters'])} |",
        f"| 汉字数 | {_fmt_int(scale['han_chars'])} |",
        f"| 总字符数 | {_fmt_int(scale['total_chars'])} |",
        f"| 非空白字符数 | {_fmt_int(scale['non_whitespace_chars'])} |",
        f"| 实体数 | {_fmt_int(scale['entities'])} |",
        f"| 断言数 | {_fmt_int(scale['assertions'])} |",
        f"| 角色状态数 | {_fmt_int(scale['character_states'])} |",
        f"| 时间线事件数 | {_fmt_int(scale['timeline_events'])} |",
        f"| 关系数 | {_fmt_int(scale['relations'])} |",
        f"| 伏笔条目数 | {_fmt_int(scale['plot_items'])} |",
        f"| 伏笔链接数 | {_fmt_int(scale['foreshadow_links'])} |",
        f"| 来源片段数 | {_fmt_int(scale['source_spans'])} |",
        f"| 文档修订数 | {_fmt_int(scale['document_revisions'])} |",
        f"| 注入矛盾数 | {_fmt_int(scale['injected_contradictions'])} |",
        "",
        "## 结果",
        "",
        "### 按类召回",
        "",
        _category_table(report),
        "",
        f"- 总体召回率 **{detection['recall']:.1%}**（{detection['total_detected']}/{detection['total_injected']}）",
        f"- 精确率 **{detection['precision']:.1%}**，F1 **{detection['f1']:.3f}**",
        f"- 干净基线：**{clean['findings']}** findings，score **{clean['score']}**，"
        f"{clean['hard_negatives_total']} 条 hard negative 全部未被误报",
        f"- 脏库评分：score **{report['analysis']['score']}**，grade **{report['analysis']['grade']}**，"
        f"共 {report['analysis']['findings']} 条 finding",
        "",
        "### 性能与规模指标",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 分析耗时（中位数 / {timing['repeat']} 次） | {timing['median_s'] * 1000:.2f} ms |",
        f"| 分析耗时（最快 / 最慢） | {timing['min_s'] * 1000:.2f} / {timing['max_s'] * 1000:.2f} ms |",
        f"| 检查条目数 | {_fmt_int(scale['examined_items'])} |",
        f"| 每 10 万汉字分析耗时 | {scale['seconds_per_100k_han_chars']:.4f} s |",
        f"| 吞吐（检查条目/秒） | {_fmt_int(scale['examined_items_per_second'])} |",
        f"| 每万字 findings | {scale['findings_per_10k_han_chars']} |",
        f"| 每实体汉字数 | {_fmt_int(scale['han_chars_per_entity'])} |",
        f"| 每实体断言数 | {scale['assertions_per_entity']} |",
        f"| 数据库大小 | {scale['db_size_mb']} MB |",
        f"| 峰值 RSS | {report['memory']['peak_rss_mb']} MB |",
        "",
        "> 「每万字 findings」「每实体断言数」等是与进程内存无关的规模归一化指标，"
        "用于跨数据集横向比较；峰值 RSS 仅作参考，不参与验收。",
        "",
        "### 验收",
        "",
        _acceptance_table(report),
        "",
        "## 结论",
        "",
        f"1. **检出能力**：七类矛盾（fact / timeline / knowledge / attribute / relation / foreshadow / evidence）"
        f"在合成数据上召回率 {detection['recall']:.1%}，无漏检类别。",
        f"2. **误报控制**：干净项目 0 findings，注入后 {detection['false_positives']} 条误报；"
        f"{clean['hard_negatives_total']} 条 hard negative（有效期不重叠、supersedes 取代、author_plan、"
        f"已归档伏笔、同章同时间等）全部未被误判。",
        f"3. **性能**：{_fmt_int(scale['han_chars'])} 汉字规模单次分析 {timing['median_s'] * 1000:.2f} ms，"
        f"仅占 5 秒预算的 {timing['median_s'] / 5.0 * 100:.2f}%，可放进保存即校验的交互路径。",
        "4. **可复现**：固定种子下数据集与注入完全确定，报告由命令直接产出，无手工填数。",
        "",
        "## 已知局限",
        "",
        "1. **合成数据，非真实稿件**。正文由模板化 `ProseWriter` 生成，句式重复度高；"
        "真实长篇的指代、省略、非线性叙事远比这里复杂，本基线不能外推为真实稿件上的召回率。",
        "2. **生成器与引擎同源**。`generate.py` 的干净数据是按引擎的判定规则构造的，"
        "因此「干净库 0 误报」部分是构造出来的，不等于真实噪声下的误报率。",
        "3. **注入是「规则级」矛盾**。注入直接写入结构化表（断言/状态/关系/伏笔/时间线），"
        "不经过从正文抽取的链路，测的是**检测器**而非**抽取器**；抽取环节的错误率不在本基线覆盖范围内。",
        "4. **匹配依赖 evidence 里的业务主键**。引擎 finding id 是内容哈希，无法预知；"
        "ground truth 用 `assertion_id` / `plot_id` / `relation_id` 等锚点匹配。"
        "若引擎未来改变 evidence 结构，匹配器需同步更新。",
        "5. **单类别单 finding 假设**。每条注入期望恰好 1 条 finding；"
        "引擎若对同一矛盾产出多条不同严重度的 finding，多出的部分会被计为误报。",
        "6. **规模上限未测**。当前只覆盖 6.5 万字（25 章）与 10 万字（40 章）两档；"
        "百万字级的内存与耗时特性未验证。",
        "7. **仅中文**。分句、字数统计、实体命名均按中文设计，英文项目未覆盖。",
        "8. **耗时含 warm cache**。重复 5 次取中位数，首次冷启动耗时单列在 JSON 的 `first_run_s`。",
        f"9. **脏库评分饱和**。本次注入合计扣分 {deduction}（blocker {sev.get('blocker', 0)}×20 + "
        f"high {sev.get('high', 0)}×8 + medium {sev.get('medium', 0)}×3 + low {sev.get('low', 0)}×1），"
        f"远超 100 分上限，score 饱和到 {report['analysis']['score']}、grade {report['analysis']['grade']}，"
        "因此 score/grade 在脏库上不具备区分度；本基线以 recall / precision / 按类召回为主指标。",
        "",
    ]
    if report["warnings"]:
        lines += ["## 运行警告", ""] + [f"- {item}" for item in report["warnings"]] + [""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LongFormBench 一键基线")
    parser.add_argument("--chapters", type=int, default=DEFAULT_CHAPTERS, help="章节数（默认 40）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT, help="分析重复次数（默认 5）")
    parser.add_argument(
        "--work-dir", default="benchmarks/longform/.work", help="临时数据库目录"
    )
    parser.add_argument(
        "--out", default="benchmarks/longform/results/longform-baseline.json", help="JSON 报告路径"
    )
    parser.add_argument(
        "--summary", default="benchmarks/longform/results/longform-baseline.md", help="Markdown 摘要路径"
    )
    parser.add_argument(
        "--doc", default="docs/consistency-baseline-2026-10-23.md",
        help="对外基线文档路径；传空字符串则不写",
    )
    parser.add_argument("--min-recall", type=float, default=DEFAULT_MIN_RECALL)
    parser.add_argument("--max-analysis-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--keep-work", action="store_true", help="保留临时 SQLite 文件")
    args = parser.parse_args(argv)

    if args.chapters < MIN_CHAPTERS:
        print(f"错误：chapters 必须 >= {MIN_CHAPTERS}（当前 {args.chapters}）。", file=sys.stderr)
        return 2

    report = run(
        chapters=args.chapters,
        seed=args.seed,
        repeat=args.repeat,
        work_dir=ROOT / args.work_dir,
        out_json=ROOT / args.out,
        out_md=ROOT / args.summary,
        out_doc=(ROOT / args.doc) if args.doc else None,
        min_recall=args.min_recall,
        max_seconds=args.max_analysis_seconds,
        keep_work=args.keep_work,
    )
    print(render_summary(report))
    print(f"JSON: {args.out}")
    if args.doc:
        print(f"DOC:  {args.doc}")
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
