"""在真实公版长篇上跑一致性验收：抽取 → 落库 → 引擎 → 注入回检。

这个脚本关掉的是 `docs/CHANGELOG.md` 里那条一直挂着的未达成——
「30 万字 × 3 部真实作品的一致性检查」。三本书分别 84.2 万 / 59.5 万 / 90.6 万字，
都远超 30 万字；断言来自 `extract_rules.py` 在正文上的确定性抽取，落库走生产
schema 与生产 `create_chapter()`，引擎调用生产 `analyze()`。

**它不是 LLM 抽取验收。** 规则抽取的密度很低（见报告 §3），本脚本量的是
「引擎在真实作品、真实事实形状上的表现」，不是「抽取器有多聪明」。

两道检查：
1. **误报**：真实作品本身自洽，引擎在真实断言上应尽量少报。报出来的每一条都
   要人工回原文核对，脚本把它们全部打印出来交给人看。
2. **召回**：往真实断言里注入已知矛盾（同一主语、同一谓词、同一属性槽位但取值
   不同），量引擎能不能全部抓到。注入点确定性可复算。
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BENCH_DIR))

from backend import server  # noqa: E402
from backend.moyu_consistency import analyze  # noqa: E402

from extract_rules import extract, read_book  # noqa: E402

NOW = "2026-10-24T00:00:00+08:00"
REVIEWER = "rule-extractor"
PROJECT = "default-project"



def build_db(db_path: Path) -> sqlite3.Connection:
    """用生产的 `init_db()` 建库。

    手抄 `CREATE TABLE app_*` 会随 `backend/server.py` 漂移（上一版就漏了
    `app_state_version` / `app_chapter_meta` 而 `create_chapter()` 直接炸），所以这里
    改 `DB_PATH` 后调用真正的初始化函数。
    """
    if db_path.exists():
        db_path.unlink()
    server.DB_PATH = db_path
    server.init_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def seed_scope(conn: sqlite3.Connection, branch: str, name: str) -> None:
    if conn.execute("SELECT 1 FROM project WHERE id=?", (PROJECT,)).fetchone() is None:
        conn.execute(
            "INSERT INTO project(id,schema_version,name,created_at) VALUES(?,?,?,?)",
            (PROJECT, 1, name, NOW),
        )
    if conn.execute("SELECT 1 FROM branch WHERE id=?", (branch,)).fetchone() is None:
        conn.execute(
            "INSERT INTO branch(id,project_id,parent_id,merge_base_revision,head_revision,status,acl_json)"
            " VALUES(?,?,?,?,?,?,?)",
            (branch, PROJECT, None, None, None, "active", "{}"),
        )


def load_chapters(conn: sqlite3.Connection, branch: str, book: str, chapters: list[tuple[int, str]]) -> dict[int, str]:
    """用生产 `create_chapter()` 建章，返回 回次→章节 id。"""
    ids: dict[int, str] = {}
    # `create_chapter()` 从模块级 `branch_id()` 取分支，不读 payload——必须切全局，
    # 否则三本书的章节会全部落进 `main`，分支隔离失效。
    original = server.BRANCH_ID
    server.BRANCH_ID = branch
    try:
        for ordinal, body in chapters:
            chapter_id = f"ch_{branch}_{ordinal:04d}"
            server.create_chapter(conn, {"id": chapter_id, "title": f"第{ordinal}回", "body": body})
            ids[ordinal] = chapter_id
    finally:
        server.BRANCH_ID = original
    conn.commit()
    return ids


def insert_assertions(conn: sqlite3.Connection, branch: str, book: str, facts: list[Any],
                      chapter_ids: dict[int, str], entity_cache: dict[str, str]) -> int:
    written = 0
    for fact in facts:
        if fact.chapter_ordinal not in chapter_ids:
            continue
        subject_key = f"{branch}:{fact.subject}"
        entity_id = entity_cache.get(subject_key)
        if entity_id is None:
            entity_id = f"ent_{branch}_{len(entity_cache):05d}"
            conn.execute(
                "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,lineage_parent_id,status)"
                " VALUES(?,?,?,?,?,?,?)",
                (entity_id, PROJECT, "character", fact.subject, "[]", None, "active"),
            )
            entity_cache[subject_key] = entity_id
        conn.execute(
            "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
            "epistemic_mode,state,confidence,evidence_json,reviewed_by,reviewed_at,valid_start,valid_end)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"as_{branch}_{written:06d}", PROJECT, branch, entity_id, fact.predicate, fact.object_json,
                "world_truth", "canon", 0.9,
                json.dumps([{"chapter_id": chapter_ids[fact.chapter_ordinal], "quote": fact.quote}], ensure_ascii=False),
                REVIEWER, NOW, None, None,
            ),
        )
        written += 1
    conn.commit()
    return written


def insert_timeline(conn: sqlite3.Connection, branch: str, book: str, chapters: list[tuple[int, str]],
                    chapter_ids: dict[int, str]) -> int:
    """回目本身就是事件的时序锚——每回一条，序位就是回次。"""
    count = 0
    for ordinal, body in chapters:
        title = f"{book} 第{ordinal}回"
        conn.execute(
            "INSERT INTO app_timeline_event(id,project_id,branch_id,title,event_time,chapter_id,entity_ids_json,"
            "summary,status,ordinal,created_at,updated_at,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"tl_{branch}_{ordinal:04d}", PROJECT, branch, title, "", chapter_ids[ordinal], "[]",
             "", "planned", ordinal, NOW, NOW, "{}"),
        )
        count += 1
    conn.commit()
    return count


def _evidence_assertion_ids(report: dict) -> set[str]:
    """把 finding 里出现过的断言 id 全收出来。

    引擎把配对 id 放在 `evidence["assertion_ids"]`（列表）；早期版本/其他类别用
    `assertion_id` 单值。只认某一种键会让召回率被算成 0——本脚本第一版就踩了
    这个坑，所以这里按键名扫，不写死。
    """
    ids: set[str] = set()
    for finding in report["findings"]:
        evidence = finding.get("evidence") or {}
        for key, value in evidence.items():
            if isinstance(value, str) and key.endswith("id"):
                ids.add(value)
            elif isinstance(value, list) and "id" in key:
                ids.update(item for item in value if isinstance(item, str))
    return ids


def plan_injections(
    capacities: list[int], inject_count: int = 40, inject_total: int | None = None
) -> list[int]:
    """把注入预算分配到各书；总量模式对小池封顶后再均摊余额。

    奇数余量优先给剩余容量更大的书，容量相同则保持输入顺序。这样分配只依赖
    容量、总量和书序，不依赖随机抽样，也不会因某书事实池较小而少注入。
    """
    if any(capacity < 0 for capacity in capacities):
        raise ValueError("capacities 不能为负数")
    if inject_total is None:
        if inject_count < 0:
            raise ValueError("--inject 不能为负数")
        return [min(inject_count, capacity) for capacity in capacities]
    if inject_total < 0:
        raise ValueError("--inject-total 不能为负数")
    if inject_total > sum(capacities):
        raise ValueError("--inject-total 超过所有书的事实池容量总和")

    plan = [0] * len(capacities)
    remaining = inject_total
    while remaining:
        active = [i for i, capacity in enumerate(capacities) if plan[i] < capacity]
        share, extra = divmod(remaining, len(active))
        bonus_order = sorted(active, key=lambda i: (-(capacities[i] - plan[i]), i))
        bonuses = set(bonus_order[:extra])
        for i in active:
            requested = share + (1 if i in bonuses else 0)
            allocated = min(requested, capacities[i] - plan[i])
            plan[i] += allocated
            remaining -= allocated
    return plan


def injection_pool(conn: sqlite3.Connection, branch: str) -> list[tuple[Any, dict]]:
    """返回与实际注入共用的候选池，避免容量规划与抽样口径漂移。"""
    rows = conn.execute(
        "SELECT id,subject_entity_id,predicate,object_json FROM assertion "
        "WHERE project_id=? AND branch_id=? AND state='canon' ORDER BY id",
        (PROJECT, branch),
    ).fetchall()
    pool = []
    for row in rows:
        payload = json.loads(row["object_json"])
        # 只要求是结构化对象且有 value：带 attribute 的按属性槽位比，不带槽位的
        # 单值谓词（has_name 等）同样「两个取值即冲突」，都能作为注入点。
        if isinstance(payload, dict) and payload.get("value"):
            pool.append((row, payload))
    return pool


def inject_contradictions(conn: sqlite3.Connection, branch: str, count: int, seed: int) -> list[dict]:
    """在真实断言里注入已知矛盾：同主语同谓词同槽位、取值不同。"""
    pool = injection_pool(conn, branch)
    if not pool:
        return []
    picked = random.Random(seed).sample(pool, min(count, len(pool)))
    injected = []
    for index, (row, payload) in enumerate(picked):
        forged = dict(payload)
        forged["value"] = f"{payload['value']}（注入取值）"
        new_id = f"as_inj_{branch}_{index:04d}"
        conn.execute(
            "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
            "epistemic_mode,state,confidence,evidence_json,reviewed_by,reviewed_at,valid_start,valid_end)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id, PROJECT, branch, row["subject_entity_id"], row["predicate"],
                json.dumps(forged, ensure_ascii=False, sort_keys=True),
                "world_truth", "canon", 0.9,
                json.dumps([{"quote": "注入用：无原文出处"}], ensure_ascii=False),
                REVIEWER, NOW, None, None,
            ),
        )
        injected.append({"source_id": row["id"], "injected_id": new_id, "attribute": payload.get("attribute"),
                         "original": payload.get("value"), "forged": forged["value"]})
    conn.commit()
    return injected


def scale_curve(conn: sqlite3.Connection, branch: str, sizes: list[int], stress_db: Path) -> list[dict]:
    """在**独立干净库**上测规模曲线，只量引擎耗时。

    每个尺寸都从干净库备份一份，把该分支的 canon 断言循环复制到 N 条。复制品取值
    与原断言完全相同，按同一实体/谓词/槽位分组后应当 0 冲突——所以这里量的不是
    数据质量，而是「断言涨到万级时引擎会不会退化成 N²」，也就是合成数据里那 45 条
    假阳的复发风险。

    为什么不能就地放大验收库：注入矛盾之后再复制，每条伪造取值会与全部副本两两
    配对，问题数按 副本数×伪造数 爆炸（实测 13,590 条时 583,200 条问题），那是
    「注入密度」被放大，不是引擎本身退化，结论会失真。
    """
    rows = conn.execute(
        "SELECT id,subject_entity_id,predicate,object_json,evidence_json FROM assertion "
        "WHERE project_id=? AND branch_id=? AND state='canon'",
        (PROJECT, branch),
    ).fetchall()
    if not rows:
        return []
    out = []
    for size in sizes:
        db_file = stress_db.with_name(f"{stress_db.stem}-{size}.sqlite3")
        for suffix in ("", "-wal", "-shm"):
            candidate = db_file.with_name(db_file.name + suffix)
            if candidate.exists():
                candidate.unlink()
        dest = sqlite3.connect(db_file)
        conn.backup(dest)
        written = 0
        index = 0
        while written < size:
            for row in rows:
                if written >= size:
                    break
                dest.execute(
                    "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
                    "epistemic_mode,state,confidence,evidence_json,reviewed_by,reviewed_at,valid_start,valid_end)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"as_scale_{index}", PROJECT, branch, row["subject_entity_id"], row["predicate"],
                     row["object_json"], "world_truth", "canon", 0.9, row["evidence_json"],
                     REVIEWER, NOW, None, None),
                )
                written += 1
                index += 1
            if written == 0:
                break
        dest.commit()
        total = dest.execute(
            "SELECT COUNT(*) FROM assertion WHERE project_id=? AND branch_id=? AND state='canon'",
            (PROJECT, branch),
        ).fetchone()[0]
        started = time.perf_counter()
        report = analyze(dest, PROJECT, branch)
        seconds = time.perf_counter() - started
        out.append({"assertions": total, "analyze_seconds": round(seconds, 4),
                    "findings": len(report["findings"]), "score": report["score"],
                    "examined": report["total_examined"]})
        dest.close()
    return out


def scale_curve_unique(conn: sqlite3.Connection, branch: str, sizes: list[int], stress_db: Path) -> list[dict]:
    """对照曲线：断言条数同规模增长，但**每条断言挂在各自独立的实体上**。

    与 scale_curve 的区别只有一个：不制造「同一实体同一槽位 181 条副本」。如果这条
    曲线近似线性，就说明引擎的耗时增长来自**组内条数**（同实体同谓词同槽位），而不是
    表里断言总数——真正要警惕的是后者，因为真实稿件不会给同一属性写 181 条。
    """
    entity_row = conn.execute(
        "SELECT * FROM entity WHERE project_id=? LIMIT 1", (PROJECT,)
    ).fetchone()
    assertion_row = conn.execute(
        "SELECT * FROM assertion WHERE project_id=? AND branch_id=? AND state='canon' LIMIT 1",
        (PROJECT, branch),
    ).fetchone()
    if entity_row is None or assertion_row is None:
        return []
    entity_columns = list(entity_row.keys())
    assertion_columns = list(assertion_row.keys())
    entity_values = list(entity_row)
    assertion_values = list(assertion_row)
    assertion_index = {name: position for position, name in enumerate(assertion_columns)}

    out = []
    for size in sizes:
        db_file = stress_db.with_name(f"{stress_db.stem}-unique-{size}.sqlite3")
        for suffix in ("", "-wal", "-shm"):
            candidate = db_file.with_name(db_file.name + suffix)
            if candidate.exists():
                candidate.unlink()
        dest = sqlite3.connect(db_file)
        conn.backup(dest)
        placeholders = ",".join("?" for _ in entity_columns)
        assertion_placeholders = ",".join("?" for _ in assertion_columns)
        for index in range(size):
            entity_id = f"ent_scale_{index}"
            values = list(entity_values)
            values[entity_columns.index("id")] = entity_id
            dest.execute(f"INSERT INTO entity({','.join(entity_columns)}) VALUES({placeholders})", values)

            row_values = list(assertion_values)
            row_values[assertion_index["id"]] = f"as_unique_{index}"
            row_values[assertion_index["subject_entity_id"]] = entity_id
            dest.execute(
                f"INSERT INTO assertion({','.join(assertion_columns)}) VALUES({assertion_placeholders})",
                row_values,
            )
        dest.commit()
        total = dest.execute(
            "SELECT COUNT(*) FROM assertion WHERE project_id=? AND branch_id=? AND state='canon'",
            (PROJECT, branch),
        ).fetchone()[0]
        started = time.perf_counter()
        report = analyze(dest, PROJECT, branch)
        seconds = time.perf_counter() - started
        out.append({"assertions": total, "analyze_seconds": round(seconds, 4),
                    "findings": len(report["findings"]), "score": report["score"]})
        dest.close()
    return out


def run(corpus: Path, books: list[str], db_path: Path, out_path: Path, inject_count: int, seed: int,
        scale_sizes: list[int] | None = None, inject_total: int | None = None) -> dict:
    conn = build_db(db_path)
    data = extract(corpus, books)

    facts_by_book: dict[str, list[Any]] = {}
    for fact in data["facts"]:
        facts_by_book.setdefault(fact.book, []).append(fact)

    entity_cache: dict[str, str] = {}
    per_book = []
    for index, entry in enumerate(data["books"], start=1):
        book = entry["book"]
        branch = "main" if index == 1 else f"book{index}"
        seed_scope(conn, branch, book)
        chapters = read_book(corpus, book)
        chapter_ids = load_chapters(conn, branch, book, chapters)
        written = insert_assertions(conn, branch, book, facts_by_book.get(book, []), chapter_ids, entity_cache)
        timeline = insert_timeline(conn, branch, book, chapters, chapter_ids)
        per_book.append({"book": book, "branch": branch, "chars": entry["chars"],
                         "assertions": written, "timeline_events": timeline,
                         "unique_subjects": entry["unique_subjects"]})

    # 阶段一：真实断言上的误报检查
    started = time.perf_counter()
    baseline = analyze(conn, PROJECT, "main")
    baseline_all = {row["branch"]: analyze(conn, PROJECT, row["branch"]) for row in per_book}
    elapsed = time.perf_counter() - started

    # 阶段二：注入已知矛盾后量召回
    injected_total = []
    stress = None
    if scale_sizes:
        biggest = max(per_book, key=lambda row: row["assertions"])
        stress_db = db_path.with_name(db_path.stem + "-stress.sqlite3")
        curve = scale_curve(conn, biggest["branch"], scale_sizes, stress_db)
        unique_curve = scale_curve_unique(conn, biggest["branch"], scale_sizes, stress_db)
        stress = {"basis_book": biggest["book"], "curve": curve, "unique_subject_curve": unique_curve}

    recall_rows = []
    capacities = [len(injection_pool(conn, row["branch"])) for row in per_book]
    injection_plan = plan_injections(capacities, inject_count, inject_total)
    for index, row in enumerate(per_book, start=1):
        injected = inject_contradictions(conn, row["branch"], injection_plan[index - 1], seed + index)
        if not injected:
            continue
        report = analyze(conn, PROJECT, row["branch"])
        reported_ids = _evidence_assertion_ids(report)
        hit = sum(1 for item in injected if item["injected_id"] in reported_ids)
        injected_total.extend(injected)
        recall_rows.append({
            "book": row["book"], "branch": row["branch"],
            "injected": len(injected), "detected": hit,
            "recall": round(hit / len(injected), 4) if injected else None,
            "findings_after_injection": len(report["findings"]),
            "score_after_injection": report["score"],
        })


    result = {
        "scale_stress": stress,
        "corpus": {"books": [row["book"] for row in per_book],
                   "chapter_count": sum(len(read_book(corpus, row["book"])) for row in per_book),
                   "total_chars": sum(row["chars"] for row in per_book)},
        "extraction": {"per_book": per_book, "total_assertions": sum(row["assertions"] for row in per_book)},
        "baseline": {
            "per_branch": {row["branch"]: {
                "score": baseline_all[row["branch"]]["score"],
                "grade": baseline_all[row["branch"]]["grade"],
                "findings": len(baseline_all[row["branch"]]["findings"]),
                "examined": baseline_all[row["branch"]]["total_examined"],
            } for row in per_book},
            "analyze_seconds_all_branches": round(elapsed, 4),
        },
        "recall": recall_rows,
        "injection_plan": [
            {"book": row["book"], "capacity": capacity, "planned": planned}
            for row, capacity, planned in zip(per_book, capacities, injection_plan)
        ],
        "inject_total_requested": inject_total,
        "injected_count": len(injected_total),
        "injected": injected_total,
        "db_bytes": db_path.stat().st_size,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实长篇一致性验收")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--book", action="append", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--inject", type=int, default=40, help="每部注入多少条已知矛盾")
    parser.add_argument("--inject-total", type=int, default=None, help="三书合计注入多少条（按事实池容量分配）")
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--scale-sizes", default="", help="逗号分隔的断言条数，逐档在独立干净库上压测耗时，例如 2000,5000,10000,20000")
    args = parser.parse_args(argv)

    sizes = [int(part) for part in args.scale_sizes.split(",") if part.strip()]
    result = run(Path(args.corpus), args.book, Path(args.db), Path(args.out), args.inject, args.seed,
                sizes, args.inject_total)

    print("== 抽取与落库 ==")
    for row in result["extraction"]["per_book"]:
        print(f"  {row['book']}: {row['chars']} 字 / 断言 {row['assertions']} / 时间线 {row['timeline_events']} / 主语 {row['unique_subjects']}")
    print(f"  合计断言 {result['extraction']['total_assertions']}")
    print("== 真实断言上的基线（误报检查）==")
    for branch, row in result["baseline"]["per_branch"].items():
        print(f"  {branch}: 分数 {row['score']} / {row['grade']} / 问题 {row['findings']} / 核对 {row['examined']}")
    print(f"  全部分支 analyze 耗时 {result['baseline']['analyze_seconds_all_branches']}s")
    if result.get("scale_stress"):
        stress = result["scale_stress"]
        print(f"== 规模曲线（独立干净库，基准书 {stress['basis_book']}）==")
        for row in stress["curve"]:
            print(f"  同槽位副本  {row['assertions']:>6} 条断言 / analyze {row['analyze_seconds']:>7}s / 问题 {row['findings']} / 分数 {row['score']}")
        for row in stress.get("unique_subject_curve") or []:
            print(f"  独立实体    {row['assertions']:>6} 条断言 / analyze {row['analyze_seconds']:>7}s / 问题 {row['findings']} / 分数 {row['score']}")
    print("== 注入矛盾后的召回 ==")
    for row in result["recall"]:
        print(f"  {row['book']}: 注入 {row['injected']} 检出 {row['detected']} 召回 {row['recall']}")
    print(f"  合计注入 {result['injected_count']} / 请求 {result['inject_total_requested']}")
    for row in result["injection_plan"]:
        print(f"  分配 {row['book']}: 容量 {row['capacity']} 计划 {row['planned']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
