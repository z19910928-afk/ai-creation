"""在真实公版长篇上跑「章节 → 结构化断言」抽取，输出 JSON + 统计。

用法（默认离线规则模式，不需要任何凭据）::

    .venv/bin/python benchmarks/real_corpus/extract.py --limit 20 --json /tmp/extract-out.json

真机跑 LLM 抽取时先导出三个环境变量，再 `--mode openai`::

    export MOYU_EXTRACT_BASE_URL=https://api.deepseek.com/v1
    export MOYU_EXTRACT_MODEL=deepseek-chat
    export MOYU_EXTRACT_API_KEY=...
    .venv/bin/python benchmarks/real_corpus/extract.py --mode openai --limit 20 --json /tmp/llm.json

只读、不写库：这个脚本不碰 SQLite、不碰 `backend/server.py`，只把
`backend/moyu_extract.py` 抽出来的断言写成 JSON，供下游（一致性基准、审查）复算。

统计口径要说清
--------------
- `assertions`：通过 schema 校验 **且** 逐字证据比对的断言条数（这才是能入库的）。
- `rejected`：被丢弃的条数；`rejected_reasons` 按原因码分类。规则模式也会产生
  rejected（例如「年方二十五岁」前面 30 字内没有全名，主语无法确定就跳过）。
- `characters`：读入的正文字符数（`len(text)`，不是字节）。
- `elapsed_s`：抽取墙钟；分 `extract_ms`（引擎调用）与 `total_ms`（含读文件/写 JSON）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import moyu_extract  # noqa: E402

DEFAULT_CORPUS = Path("/tmp/moyu-real-corpus/corpus")
MANIFEST_NAME = "manifest.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_manifest(corpus: Path, explicit: Path | None) -> dict[str, Any]:
    """读语料清单。优先 `--manifest`，其次 `<corpus>/manifest.json`。"""
    candidates = [explicit] if explicit else []
    candidates.append(corpus / MANIFEST_NAME)
    candidates.append(ROOT / "benchmarks/real_corpus/corpus-manifest-2026-10-24.json")
    for candidate in candidates:
        if candidate and candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise SystemExit(f"找不到语料清单：试过 {[str(p) for p in candidates if p]}")


def iter_chapters(corpus: Path, manifest: dict[str, Any], books: list[str],
                  limit: int | None) -> Iterable[dict[str, Any]]:
    """按清单顺序产出章节；`limit` 是**全局**章数上限（跨书累计）。"""
    emitted = 0
    for book_entry in manifest["books"]:
        book = book_entry["book"]
        if books and book not in books:
            continue
        for chapter in book_entry["chapters"]:
            if limit is not None and emitted >= limit:
                return
            yield {"book": book, "chapter": chapter}
            emitted += 1


def read_chapter(corpus: Path, entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """读一章正文并做完整性校验（清单里记了 sha256 就必须对上）。"""
    chapter = entry["chapter"]
    path = corpus / chapter["file"]
    if not path.is_file():
        raise SystemExit(f"章节文件不存在：{path}（用 --corpus 指定语料根目录）")
    raw = path.read_bytes()
    actual = sha256_bytes(raw)
    expected = chapter.get("sha256")
    if expected and actual != expected:
        raise SystemExit(f"章节内容与清单不符：{path} sha256 {actual} != {expected}")
    text = raw.decode("utf-8")
    chapter_id = f"{entry['book']}/{chapter['ordinal']:03d}"
    meta = {
        "chapter": chapter_id,
        "book": entry["book"],
        "ordinal": chapter["ordinal"],
        "title": chapter.get("title", ""),
        "chars": len(text),
        "sha256": actual,
        "sha256_ok": True,
    }
    return text, meta


def run_extraction(
    corpus: Path,
    manifest: dict[str, Any],
    *,
    books: list[str],
    limit: int | None,
    provider: Any,
    mode_label: str,
    repeat: int = 2,
) -> dict[str, Any]:
    started = time.perf_counter()
    chapters: list[dict[str, Any]] = []
    digests: list[str] = []
    per_book: dict[str, dict[str, Any]] = {}
    extract_ms_total = 0.0

    for round_index in range(repeat):
        chapter_digests: list[str] = []
        if round_index == 0:
            chapters = []
        for entry in iter_chapters(corpus, manifest, books, limit):
            text, meta = read_chapter(corpus, entry)
            began = time.perf_counter()
            report = moyu_extract.extract_chapter_report(
                text, chapter_id=meta["chapter"], provider=provider,
            )
            elapsed_ms = (time.perf_counter() - began) * 1000.0
            digest = moyu_extract.digest(report["assertions"])
            chapter_digests.append(digest)
            if round_index > 0:
                continue
            extract_ms_total += elapsed_ms
            bucket = per_book.setdefault(meta["book"], {
                "book": meta["book"], "chapters": 0, "characters": 0,
                "assertions": 0, "rejected": 0, "rejected_reasons": {},
                "attributes": {}, "elapsed_s": 0.0,
            })
            bucket["chapters"] += 1
            bucket["characters"] += report["characters"]
            bucket["assertions"] += len(report["assertions"])
            bucket["rejected"] += len(report["rejected"])
            bucket["elapsed_s"] += elapsed_ms / 1000.0
            for reason, count in report["rejected_reasons"].items():
                bucket["rejected_reasons"][reason] = bucket["rejected_reasons"].get(reason, 0) + count
            for assertion in report["assertions"]:
                slot = assertion["attribute"] or assertion["predicate"]
                bucket["attributes"][slot] = bucket["attributes"].get(slot, 0) + 1
            chapters.append({
                **meta,
                "assertions": len(report["assertions"]),
                "rejected": len(report["rejected"]),
                "rejected_reasons": report["rejected_reasons"],
                "audit": report["assertions"][:3],
                "elapsed_ms": round(elapsed_ms, 3),
                "digest": digest,
            })
        digests.append(sha256_bytes("\n".join(chapter_digests).encode("utf-8")))

    total_ms = (time.perf_counter() - started) * 1000.0
    totals = {
        "chapters": sum(bucket["chapters"] for bucket in per_book.values()),
        "characters": sum(bucket["characters"] for bucket in per_book.values()),
        "assertions": sum(bucket["assertions"] for bucket in per_book.values()),
        "rejected": sum(bucket["rejected"] for bucket in per_book.values()),
        "rejected_reasons": _merge_reasons(per_book),
        "attributes": _merge_counts(per_book, "attributes"),
    }
    elapsed_values = [chapter["elapsed_ms"] for chapter in chapters]
    return {
        "chapters": chapters,
        "books": sorted(per_book.values(), key=lambda item: (-item["chapters"], item["book"])),
        "totals": totals,
        "determinism": {
            "repeat": repeat,
            "identical": len(set(digests)) == 1,
            "digest": digests[0] if digests else "",
        },
        "timing": {
            "extract_ms": round(extract_ms_total, 3),
            "total_ms": round(total_ms, 3),
            "per_chapter_ms": {
                "mean": round(statistics.fmean(elapsed_values), 3) if elapsed_values else 0.0,
                "max": round(max(elapsed_values), 3) if elapsed_values else 0.0,
            },
        },
        "mode": mode_label,
    }


def _merge_reasons(per_book: dict[str, dict[str, Any]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for bucket in per_book.values():
        for reason, count in bucket["rejected_reasons"].items():
            merged[reason] = merged.get(reason, 0) + count
    return dict(sorted(merged.items()))


def _merge_counts(per_book: dict[str, dict[str, Any]], key: str) -> dict[str, int]:
    merged: dict[str, int] = {}
    for bucket in per_book.values():
        for name, count in bucket[key].items():
            merged[name] = merged.get(name, 0) + count
    return dict(sorted(merged.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="真实语料 → 结构化断言抽取基准")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="语料根目录（含 <book>/*.md）")
    parser.add_argument("--manifest", default=None, help="语料清单 JSON（缺省用 <corpus>/manifest.json）")
    parser.add_argument("--book", action="append", default=[], help="只跑这些书，可重复")
    parser.add_argument("--limit", type=int, default=20, help="全局章数上限（跨书累计）")
    parser.add_argument("--mode", choices=("dry-run", "openai", "auto"), default="dry-run",
                        help="dry-run=确定性规则；openai=走 MOYU_EXTRACT_* 凭据；auto=有凭据就 openai")
    parser.add_argument("--json", default=None, help="输出 JSON 路径（缺省只打印摘要）")
    parser.add_argument("--repeat", type=int, default=2, help="确定性复跑次数（默认 2）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    corpus = Path(args.corpus)
    manifest = load_manifest(corpus, Path(args.manifest) if args.manifest else None)

    provider: Any
    if args.mode == "dry-run":
        provider, mode_label = moyu_extract.dry_run_provider, "dry-run"
    elif args.mode == "openai":
        configured = moyu_extract.provider_from_env()
        if configured is None:
            print("--mode openai 但 MOYU_EXTRACT_BASE_URL/MODEL/API_KEY 不全，无法跑真实抽取。",
                  file=sys.stderr)
            return 2
        provider, mode_label = configured, "openai"
    else:
        provider, mode_label = moyu_extract.resolve_provider(None)

    started = time.perf_counter()
    result = run_extraction(
        corpus, manifest, books=args.book, limit=args.limit,
        provider=provider, mode_label=mode_label, repeat=max(1, args.repeat),
    )
    totals = result["totals"]
    payload = {
        "generated_at": "2026-10-24T00:00:00+08:00",
        "benchmark": "real-corpus-extract",
        "version": "1.0",
        "mode": mode_label,
        "corpus": {
            "root": str(corpus),
            "manifest": str(args.manifest or (corpus / MANIFEST_NAME)),
            "books": sorted({chapter["book"] for chapter in result["chapters"]}),
            "limit": args.limit,
        },
        "engine": {
            "module": "backend/moyu_extract.py",
            "version": moyu_extract.MODULE_VERSION,
            "sha256": sha256_file(Path(moyu_extract.__file__)),
            "rules": [rule.__name__ for rule in moyu_extract.RULES],
            "provider": mode_label,
        },
        "totals": totals,
        "books": result["books"],
        "chapters": result["chapters"],
        "determinism": result["determinism"],
        "timing": result["timing"],
        "wall_s": round(time.perf_counter() - started, 3),
    }

    if not args.quiet:
        print(f"mode={mode_label} chapters={totals['chapters']} characters={totals['characters']}")
        print(f"assertions={totals['assertions']} rejected={totals['rejected']} "
              f"reasons={totals['rejected_reasons'] or '{}'}")
        print(f"attributes={totals['attributes']}")
        print(f"determinism identical={result['determinism']['identical']} "
              f"digest={result['determinism']['digest'][:19]}…")
        print(f"elapsed extract={result['timing']['extract_ms']}ms total={result['timing']['total_ms']}ms")
        for bucket in result["books"]:
            print(f"  {bucket['book']}: 章 {bucket['chapters']} 断言 {bucket['assertions']} "
                  f"rejected {bucket['rejected']} 耗时 {bucket['elapsed_s']:.3f}s")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"JSON -> {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
