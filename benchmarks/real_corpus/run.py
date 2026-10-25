"""在真实公版长篇上跑端到端基准。**不调用任何模型。**

三本书（红楼梦 / 三国演义 / 水浒传，合计约 234 万字、360 回）经
`prepare.py` 归一化后，用**生产代码路径**灌进真实 schema 的 SQLite，然后测四件事：

A. 导入保真度 —— 从 `document_revision.canonical_text` 回读字节，与语料文件
   逐个比 sha256。任何编码/截断丢失都会在这里暴露。
B. 规模与耗时 —— 回数、字数、导入墙钟、库文件大小。
C. 长程检索 —— 用生产 BM25（`moyu_context.search_materials`）在全书 chunk 上
   检索：①「针」查询能否在 top-1/5/20 命中真正含有该短语的那一回；
   ②不存在的短语是否返回空（不伪造证据）；③查询延迟。
D. 一致性引擎在真实规模库上的耗时。

**这个基准测不到什么（必须写在结果里，不许含糊）**：
- 一致性引擎的**召回率**测不了。引擎读的是 `assertion` / `character_state` /
  `plot_thread` 等结构化表，而这些表要靠 AI 抽取真实稿件才能填。本机没有任何
  模型凭据（`backend/*.py` 只在 `config.get("api_key")` 读库内连接配置，本地库
  是 0 字节），所以「真实稿件 → AI 抽取 → 灌库 → 跑引擎」这条链跑不起来。
  没有断言时引擎会返回 100/A，那是**空库**的分数，不是「没有矛盾」。
- C 测的是**词面检索**在真实规模上的工程能力（能不能不丢证据、延迟多少），
  不是语义召回。同义改写、指代消解不在覆盖范围内。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend import server  # noqa: E402
from backend.moyu_consistency import analyze  # noqa: E402
from backend.moyu_context import ScopePolicy, paragraph_ranges, search_materials  # noqa: E402

NOW = "2026-01-01T00:00:00+00:00"
NEEDLE_CHARS = 24
ABSENT_PHRASE = "此句在原书中并不存在用来验证检索不会伪造证据"
DEFAULT_RELEVANCE_THRESHOLDS = (0.0, 0.01, 0.02, 0.04, 0.08, 0.16)
DEFAULT_COVERAGE_THRESHOLDS = (0.0, 0.1, 0.25, 0.5, 0.75)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def setup_database(path: Path) -> None:
    """用生产 init_db() 建库：schema 完全等于真实运行时的库。

    注意：这里**不还原** `server.DB_PATH`。生产函数（`init_db` /
    `create_chapter`）都通过这个全局读写，基准全程必须指向同一个临时库；
    之前先还原再连接，实际连到的是仓库根的空 `moyu.sqlite3`。
    """
    if path.exists():
        path.unlink()
    server.DB_PATH = path
    server.init_db()


def seed_scope(conn: sqlite3.Connection, project: str, branch: str, name: str) -> None:
    """建书的分支。

    `init_db()` 已经种了一个 `default-project` / `main`，而 `branch.id` 是全局
    主键——所以三本书共用一个项目、各自一条分支（`main` / `book2` / `book3`），
    这样每本书在引擎和检索里天然是独立作用域。
    """
    row = conn.execute("SELECT 1 FROM project WHERE id=?", (project,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO project(id,schema_version,name,created_at) VALUES(?,?,?,?)",
            (project, 1, name, NOW),
        )
    exists = conn.execute("SELECT 1 FROM branch WHERE id=?", (branch,)).fetchone()
    if exists is None:
        conn.execute(
            "INSERT INTO branch(id,project_id,parent_id,merge_base_revision,head_revision,status,acl_json)"
            " VALUES(?,?,?,?,?,?,?)",
            (branch, project, None, None, None, "active", "{}"),
        )


def load_book(text_dir: Path, project: str, branch: str, name: str) -> dict:
    """把一本书按生产 create_chapter() 逐回灌库，并回读校验保真度。"""
    files = sorted(text_dir.glob("*.md"))
    if not files:
        raise SystemExit(f"{text_dir} 里没有 .md")

    original_scope = (server.PROJECT_ID, server.BRANCH_ID)
    server.PROJECT_ID = project
    server.BRANCH_ID = branch
    chapters: list[dict] = []
    started = time.perf_counter()
    try:
        with sqlite3.connect(server.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            seed_scope(conn, project, branch, name)
            for index, path in enumerate(files, start=1):
                payload = path.read_text(encoding="utf-8")
                lines = payload.split("\n", 2)
                title = lines[0].lstrip("# ").strip()
                body = lines[2].rstrip("\n") if len(lines) > 2 else ""
                # 章节 id 必须带书标识：三本书共享一个项目，而
                # `document_node.id` 是全局主键，只用序号会冲突。
                chapter_id = f"ch_{branch}_{index:04d}"
                server.create_chapter(conn, {"id": chapter_id, "title": title, "body": body})
                chapters.append({
                    "id": chapter_id,
                    "ordinal": index,
                    "title": title,
                    "chars": len(body),
                    "file_sha256": sha256_bytes(payload.encode("utf-8")),
                    "body_sha256": sha256_bytes(body.encode("utf-8")),
                })
            conn.commit()
    finally:
        server.PROJECT_ID, server.BRANCH_ID = original_scope
    elapsed = time.perf_counter() - started

    # A. 保真度：回读 canonical_text 逐字节比对。
    mismatched = []
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for chapter in chapters:
            row = conn.execute(
                "SELECT n.id AS rid, n.canonical_text AS text"
                " FROM document_node d JOIN document_revision n ON n.document_node_id=d.id"
                " WHERE d.id=? ORDER BY n.created_at DESC, n.id DESC LIMIT 1",
                (chapter["id"],),
            ).fetchone()
            if row is None:
                mismatched.append({"chapter": chapter["id"], "reason": "没有 revision"})
                continue
            raw = row["text"]
            data = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            if sha256_bytes(data) != chapter["body_sha256"]:
                mismatched.append({"chapter": chapter["id"], "reason": "canonical_text 字节不一致"})

    return {
        "book": name,
        "project": project,
        "branch": branch,
        "chapter_count": len(chapters),
        "total_chars": sum(chapter["chars"] for chapter in chapters),
        "import_seconds": round(elapsed, 3),
        "fidelity_mismatches": mismatched,
        "chapters": chapters,
    }


def build_materials(project: str, branch: str, chapters: list[dict],
                    bodies: dict[str, str]) -> tuple[list[dict], dict]:
    """按生产切块函数把每回切成段落块，构造成检索用的材料。"""
    materials: list[dict] = []
    chapter_map: dict[str, dict] = {}
    for chapter in chapters:
        body = bodies[chapter["id"]]
        chapter_map[chapter["id"]] = {
            "id": chapter["id"],
            "chapter_number": chapter["ordinal"],
            "revision": "",
        }
        for index, (start, end) in enumerate(paragraph_ranges(body, 900)):
            chunk = body[start:end]
            if not chunk.strip():
                continue
            materials.append({
                "id": f"{chapter['id']}#{index}",
                "kind": "chapter_paragraph",
                "title": chapter["title"],
                "body": chunk,
                "chapter_number": chapter["ordinal"],
                "source_chapter_id": chapter["id"],
                "project_id": project,
                "branch_id": branch,
                "chunk_start": start,
                "chunk_end": end,
            })
    return materials, chapter_map


def pick_needles(chapters: list[dict], bodies: dict[str, str], count: int = 8) -> list[dict]:
    """在书里均匀挑几个「针」：一段正文中间的 24 字窗口，且全库唯一。"""
    if not chapters:
        return []
    step = max(1, len(chapters) // count)
    picks = chapters[::step][:count]
    everything = "\n".join(bodies.values())
    needles = []
    for chapter in picks:
        body = bodies[chapter["id"]]
        ranges = paragraph_ranges(body, 900)
        if not ranges:
            continue
        start, end = ranges[len(ranges) // 2]
        chunk = body[start:end]
        if len(chunk) < NEEDLE_CHARS * 2:
            continue
        offset = start + (len(chunk) - NEEDLE_CHARS) // 2
        needle = body[offset:offset + NEEDLE_CHARS]
        if everything.count(needle) != 1:
            continue
        needles.append({"chapter_id": chapter["id"], "ordinal": chapter["ordinal"], "needle": needle})
    return needles


def measure_retrieval(project: str, branch: str, materials: list[dict],
                      chapter_map: dict, needles: list[dict], *,
                      thresholds: tuple[float, ...] = DEFAULT_RELEVANCE_THRESHOLDS,
                      coverage_thresholds: tuple[float, ...] = DEFAULT_COVERAGE_THRESHOLDS,
                      min_recall: float = 0.95) -> dict:
    """测词面检索，并把两种下限的取舍写成可复算的 sweep。

    分数阈值是生产 BM25 分数的绝对下限，不是跨查询可比较的概率；
    coverage 阈值是命中文档覆盖查询词的比例，也只是词面契约。这里的
    ground truth 只有“针来自哪一章”，所以两边仍是 lexical proxy，
    不能被读成同义改写或指代消解的语义召回率。
    """
    if not math.isfinite(min_recall) or not 0 <= min_recall <= 1:
        raise ValueError("min_recall must be finite and between 0 and 1")
    thresholds = tuple(thresholds)
    if not thresholds or any(not math.isfinite(value) or value < 0 for value in thresholds):
        raise ValueError("thresholds must contain finite non-negative numbers")
    coverage_thresholds = tuple(coverage_thresholds)
    if not coverage_thresholds or any(
            not math.isfinite(value) or not 0 <= value <= 1 for value in coverage_thresholds):
        raise ValueError("coverage_thresholds must contain finite numbers between 0 and 1")
    policy = ScopePolicy(project_id=project, branch_id=branch)
    build_started = time.perf_counter()
    _ = search_materials(materials, "预热", policy, chapters=chapter_map, limit=1)
    build_seconds = time.perf_counter() - build_started

    def run(query: str, limit: int, min_relevance: float = 0.0,
            min_term_coverage: float = 0.0) -> dict:
        return search_materials(materials, query, policy, chapters=chapter_map,
                                limit=limit, min_relevance=min_relevance,
                                min_term_coverage=min_term_coverage)

    hits = {1: 0, 5: 0, 20: 0}
    latencies: list[float] = []
    misses = []
    for item in needles:
        started = time.perf_counter()
        result = run(item["needle"], 20)
        latencies.append((time.perf_counter() - started) * 1000)
        ranked = result["results"]
        for k in hits:
            top = ranked[:k]
            if any(entry.get("source_chapter_id") == item["chapter_id"] for entry in top):
                hits[k] += 1
            elif k == 20:
                misses.append({
                    "ordinal": item["ordinal"],
                    "needle": item["needle"],
                    "top_chapters": [entry.get("chapter_number") for entry in ranked[:5]],
                })

    empty_started = time.perf_counter()
    absent = run(ABSENT_PHRASE, 20)
    empty_ms = (time.perf_counter() - empty_started) * 1000

    total = len(needles) or 1
    sweep = []
    for threshold in thresholds:
        threshold_hits = {1: 0, 20: 0}
        returned = 0
        for item in needles:
            ranked = run(item["needle"], 20, threshold)["results"]
            returned += len(ranked)
            for k in threshold_hits:
                if any(entry.get("source_chapter_id") == item["chapter_id"] for entry in ranked[:k]):
                    threshold_hits[k] += 1
        absent_count = len(run(ABSENT_PHRASE, 20, threshold)["results"])
        recall_20 = round(threshold_hits[20] / total, 4) if needles else None
        sweep.append({
            "min_relevance": threshold,
            "queries": len(needles),
            "recall_at_1": round(threshold_hits[1] / total, 4) if needles else None,
            "recall_at_20": recall_20,
            "min_recall": min_recall,
            "recall_target_met": recall_20 >= min_recall if needles else None,
            "returned_results": returned,
            "avg_results_per_query": round(returned / len(needles), 4) if needles else None,
            "absent_phrase_result_count": absent_count,
        })
    coverage_sweep = []
    for coverage in coverage_thresholds:
        threshold_hits = {1: 0, 20: 0}
        returned = 0
        for item in needles:
            ranked = run(item["needle"], 20, min_term_coverage=coverage)["results"]
            returned += len(ranked)
            for k in threshold_hits:
                if any(entry.get("source_chapter_id") == item["chapter_id"] for entry in ranked[:k]):
                    threshold_hits[k] += 1
        absent_count = len(run(ABSENT_PHRASE, 20, min_term_coverage=coverage)["results"])
        recall_20 = round(threshold_hits[20] / total, 4) if needles else None
        coverage_sweep.append({
            "min_term_coverage": coverage,
            "queries": len(needles),
            "recall_at_1": round(threshold_hits[1] / total, 4) if needles else None,
            "recall_at_20": recall_20,
            "min_recall": min_recall,
            "recall_target_met": recall_20 >= min_recall if needles else None,
            "returned_results": returned,
            "avg_results_per_query": round(returned / len(needles), 4) if needles else None,
            "absent_phrase_result_count": absent_count,
        })
    return {
        "material_count": len(materials),
        "warmup_seconds": round(build_seconds, 3),
        "needles": len(needles),
        "recall_at_1": round(hits[1] / total, 4),
        "recall_at_5": round(hits[5] / total, 4),
        "recall_at_20": round(hits[20] / total, 4),
        "latency_ms_p50": round(statistics.median(latencies), 2) if latencies else None,
        "latency_ms_p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 2) if len(latencies) > 1 else None,
        "absent_phrase_result_count": len(absent["results"]),
        "absent_phrase_ms": round(empty_ms, 2),
        "misses": misses,
        "relevance_threshold_sweep": {
            "metric_scope": "absolute BM25 lexical score; source-chapter proxy, not semantic relevance",
            "min_recall": min_recall,
            "thresholds": list(thresholds),
            "queries": len(needles),
            "rows": sweep,
        },
        "term_coverage_sweep": {
            "metric_scope": "fraction of query terms present in a hit; lexical proxy, not semantic relevance",
            "min_recall": min_recall,
            "thresholds": list(coverage_thresholds),
            "queries": len(needles),
            "rows": coverage_sweep,
        },
    }


def parse_relevance_thresholds(raw: str) -> tuple[float, ...]:
    """Parse a comma-separated, finite and non-negative threshold sweep."""
    try:
        values = tuple(float(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("thresholds must be comma-separated numbers") from None
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise argparse.ArgumentTypeError("thresholds must be finite non-negative numbers")
    return values


def parse_coverage_thresholds(raw: str) -> tuple[float, ...]:
    """Parse a comma-separated coverage sweep in the closed interval [0, 1]."""
    try:
        values = tuple(float(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("coverage thresholds must be comma-separated numbers") from None
    if not values or any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise argparse.ArgumentTypeError("coverage thresholds must be finite numbers between 0 and 1")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实公版长篇端到端基准")
    parser.add_argument("--corpus", required=True, help="prepare.py 产出的语料目录")
    parser.add_argument("--db", required=True, help="临时 SQLite 路径（会被重建）")
    parser.add_argument("--out", required=True, help="结果 JSON 输出路径")
    parser.add_argument("--min-recall", type=float, default=0.95,
                        help="threshold sweep 的词面针召回下限，默认 0.95")
    parser.add_argument("--relevance-thresholds", type=parse_relevance_thresholds,
                        default=DEFAULT_RELEVANCE_THRESHOLDS,
                        help="逗号分隔的 BM25 绝对分数下限，默认 0,.01,.02,.04,.08,.16")
    parser.add_argument("--coverage-thresholds", type=parse_coverage_thresholds,
                        default=DEFAULT_COVERAGE_THRESHOLDS,
                        help="逗号分隔的查询词覆盖下限，范围 [0,1]，默认 0,.1,.25,.5,.75")
    args = parser.parse_args(argv)
    if not math.isfinite(args.min_recall) or not 0 <= args.min_recall <= 1:
        parser.error("--min-recall must be finite and between 0 and 1")

    corpus = Path(args.corpus)
    db_path = Path(args.db)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))

    setup_database(db_path)

    books: list[dict] = []
    for index, entry in enumerate(manifest["books"], start=1):
        book = entry["book"]
        project = "default-project"
        branch = "main" if index == 1 else f"book{index}"
        result = load_book(corpus / book, project, branch, book)
        bodies = {}
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            for chapter in result["chapters"]:
                row = conn.execute(
                    "SELECT n.canonical_text AS text FROM document_node d"
                    " JOIN document_revision n ON n.document_node_id=d.id"
                    " WHERE d.id=? ORDER BY n.created_at DESC, n.id DESC LIMIT 1",
                    (chapter["id"],),
                ).fetchone()
                raw = row["text"]
                bodies[chapter["id"]] = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        materials, chapter_map = build_materials(project, branch, result["chapters"], bodies)
        result["retrieval"] = measure_retrieval(
            project, branch, materials, chapter_map, pick_needles(result["chapters"], bodies),
            thresholds=args.relevance_thresholds,
            coverage_thresholds=args.coverage_thresholds,
            min_recall=args.min_recall,
        )
        engine_started = time.perf_counter()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            report = analyze(conn, project, branch)
        result["engine"] = {
            "seconds": round(time.perf_counter() - engine_started, 3),
            "score": report["score"],
            "grade": report["grade"],
            "findings": len(report["findings"]),
            "note": "库内无断言，分数是空库基线，不代表真实稿件的一致性水平",
        }
        result.pop("chapters", None)
        books.append(result)
        print(f"{book}: {result['chapter_count']} 回 / {result['total_chars']} 字 / "
              f"导入 {result['import_seconds']}s / 检索 R@1 {result['retrieval']['recall_at_1']} / "
              f"引擎 {result['engine']['seconds']}s")

    payload = {
        "benchmark": "real_corpus",
        "version": 1,
        "generated_at_note": "墙钟时间不进确定性摘要",
        "corpus": {"books": [entry["book"] for entry in manifest["books"]],
                   "total_chars": manifest["total_chars"],
                   "chapter_count": sum(entry["chapter_count"] for entry in manifest["books"])},
        "db_bytes": db_path.stat().st_size,
        "min_recall": args.min_recall,
        "relevance_thresholds": list(args.relevance_thresholds),
        "coverage_thresholds": list(args.coverage_thresholds),
        "books": books,
        "not_measured": [
            "一致性引擎召回率/误报率：需要 AI 抽取真实稿件生成 assertion，本机无模型凭据，未执行",
            "语义召回（同义改写、指代消解）：BM25 只做词面匹配，不在覆盖范围",
            "真实模型生成质量与成本：未调用任何模型",
        ],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"结果写入 {args.out}（库 {payload['db_bytes']} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
