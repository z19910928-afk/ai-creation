"""Executable regression fixture for the real-corpus benchmark.

Why this exists: `benchmarks/real_corpus/` needs a ~6MB third-party corpus, so it
cannot live in CI. That is exactly the condition under which a benchmark quietly
rots — it keeps "passing" because nobody runs it. This fixture pins the parts that
must not drift, using a three-chapter synthetic source:

1. `prepare.py` normalisation rules (脂批 stripped, navigation stripped, a narrative
   sentence containing 「批」 preserved, 回目 typo repaired, 1..N contiguity enforced)
2. `run.py` end-to-end: import fidelity 0 mismatches, char totals match the manifest,
   retrieval and engine sections present with the documented keys.

Run: python tests/real_corpus_fixtures.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BENCH = ROOT / "benchmarks" / "real_corpus"
BOOK = "测试书"

# 每段都要长于 2×NEEDLE_CHARS（24 字）：`pick_needles` 要求段落至少 48 字才切得出
# 一个 24 字窗口，太短的合成正文会让「针数」为 0，基准静默退化成空跑。
CH1 = (
    "第一回的正文写得长一些，好让基准能在段落中间切出一个二十四字宽的窗口。"
    "雪落在青石板上，脚步声被夜里的风一层层剥掉，只剩灯影在墙上摇晃。"
    "他记得这条巷子尽头应当有一口井，可走过去只看见半堵矮墙，墙根压着一片枯叶。"
)
CH2 = (
    "第二回的正文同样要足够长，写的是同一件旧事被重新提起，但叙述换了一个人的口吻。"
    "灯下有人翻出一本旧册子，纸页发脆，边角卷起，字迹被潮气泡得发毛。"
    "读到第三行时她停住，因为那一行写着的事，她确信自己从未做过。"
)
CH3 = (
    "第三回的正文继续加长，写主角终于承认自己记错了，语气比前两回更低。"
    "他坐在堂前的矮凳上，把袖子里的碎纸片一片片摊开，对着光看上面的墨痕。"
    "最后一片纸背面有一行小字，是他自己的笔迹，写着一个他不敢念出声的名字。"
)


def _html(title: str, *paragraphs: str) -> str:
    body = "\n".join(f"<p>{text}</p>" for text in paragraphs)
    return (
        "<html><body>"
        f'<div class="nav">← 上一回：第零回</div>'
        f"<div>{title} 原文</div>"
        f"{body}"
        '<div class="nav">首页</div>'
        '<div class="copy">© 阿星星</div>'
        "</body></html>"
    )


def build_source(root: Path) -> None:
    chapters = root / BOOK / f"原文版{BOOK}"
    chapters.mkdir(parents=True, exist_ok=True)
    # 脂批嵌在正文里；同时塞一句含「批」的正常叙事，证明不是按关键字盲删。
    narrative_with_batch = "察院便批：此案有疑，着令再审。"
    (chapters / "第一回 甲 原文.html").write_text(
        _html("第一回 甲", CH1, "```(甲戌侧批：此处伏后文)```", narrative_with_batch),
        encoding="utf-8",
    )
    (chapters / "第二回 乙 原文.html").write_text(
        _html("第二回 乙", CH2, "```(庚辰眉批：与上回呼应)```"),
        encoding="utf-8",
    )
    # 源站笔误：「第一百二回」的连续性问题由 repair_typo 之外的一致性检查兜底，
    # 这里第三回用正确写法，笔误单独在 test_repair_typo 里断言。
    (chapters / "第三回 丙 原文.html").write_text(
        _html("第三回 丙", CH3),
        encoding="utf-8",
    )


def load_prepare():
    spec = importlib.util.spec_from_file_location("real_corpus_prepare", BENCH / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repair_typo() -> None:
    """`repair_typo` 只处理**词尾**的零，且只作用于纯数字串。

    真实调用点在 `cn_to_int` 内部，而 `prepare_book` 传进去的是剥掉「第」「回」
    之后的数字串（`match.group(1)[1:-1]`），所以「第一百二零回」能被修好；
    直接对带「回」的整串调用它则不生效——这条差异曾让本 fixture 的第一版写错
    预期，记在这里防止回退。
    """
    prepare = load_prepare()
    assert prepare.repair_typo("一百二零") == "一百二十"
    assert prepare.repair_typo("第一百二零回") == "第一百二零回", "非词尾不应改动"
    assert prepare.repair_typo("一百零一") == "一百零一", "占位零不在词尾，不能动"

    assert prepare.cn_to_int("一百二十") == 120
    assert prepare.cn_to_int("一百二零") == 120, "笔误回目经 cn_to_int 应得到 120"
    assert prepare.cn_to_int("一百零一") == 101
    assert prepare.cn_to_int("二十一") == 21
    assert prepare.cn_to_int("三") == 3


def test_normalize_rules() -> None:
    prepare = load_prepare()
    title, text = prepare.normalize(_html("第一回 甲", CH1, "```(甲戌侧批：批语)```", "察院便批：此案有疑。"))

    assert title == "第一回 甲", title
    assert CH1 in text, text
    assert "甲戌侧批" not in text, "脂批没有被剔除"
    assert "批语" not in text, "脂批没有被剔除"
    # 关键：含「批」的叙事句必须留下，否则就是把正文当批语删了
    assert "察院便批" in text, "正常叙事被误删"
    # 导航与版权行必须消失
    for noise in ("上一回", "首页", "阿星星"):
        assert noise not in text, f"导航行未剔除：{noise}"


def test_prepare_end_to_end(tmp: Path) -> Path:
    source = tmp / "source"
    build_source(source)
    out = tmp / "corpus"
    proc = subprocess.run(
        [sys.executable, str(BENCH / "prepare.py"), "--source", str(source), "--book", BOOK, "--out", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"prepare.py 失败：\n{proc.stdout}\n{proc.stderr}"

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["corpus_version"] == 1
    assert manifest["total_chars"] == sum(b["total_chars"] for b in manifest["books"])
    entry = manifest["books"][0]
    assert entry["chapter_count"] == 3
    ordinals = [row["ordinal"] for row in entry["chapters"]]
    assert ordinals == [1, 2, 3], ordinals
    assert manifest["total_chars"] == entry["total_chars"], "顶层与单书字数不一致"
    assert all(len(row["sha256"]) == 64 for row in entry["chapters"]), "sha256 缺失"
    return out


def test_run_end_to_end(tmp: Path, corpus: Path) -> dict:
    db = tmp / "bench.sqlite3"
    out = tmp / "results.json"
    proc = subprocess.run(
        [sys.executable, str(BENCH / "run.py"), "--corpus", str(corpus), "--db", str(db), "--out", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"run.py 失败：\n{proc.stdout}\n{proc.stderr}"
    assert db.exists() and db.stat().st_size > 0, "库文件没有生成"

    results = json.loads(out.read_text(encoding="utf-8"))
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))

    # 导入保真度是这条链路的下限证据，必须为 0
    book = results["books"][0]
    assert book["fidelity_mismatches"] == [], book["fidelity_mismatches"]
    assert book["total_chars"] == manifest["total_chars"], (book["total_chars"], manifest["total_chars"])
    assert book["chapter_count"] == 3

    # 检索段：键名与「针数>0」都要在，否则基准静默退化成空跑
    retrieval = book["retrieval"]
    for key in ("material_count", "needles", "recall_at_1", "recall_at_20", "latency_ms_p50",
                "absent_phrase_result_count", "misses"):
        assert key in retrieval, f"检索段缺键：{key}"
    assert retrieval["material_count"] > 0, "没有段落块被索引"
    assert retrieval["needles"] > 0, "没有生成任何针"

    # 引擎段：必须显式标注「空库基线」，防止有人把 100 分当真实水平引用
    engine = book["engine"]
    assert engine["findings"] == 0, engine
    assert "空库基线" in engine["note"], engine["note"]
    return results


def test_absent_phrase_is_measured(results: dict) -> None:
    """「原书不存在的句子会返回几条」必须被持续量化。

    这里**不断言 >0**：实测该值依赖语料与查询是否共享字面。真实古籍里常用字
    遍布全书，一个不存在的句子照样能凑满 limit（3 本书都是 20 条）；而本 fixture
    的三章合成正文用字与那句查询几乎不共享，返回 0 是正确的。

    真正的缺陷证据是代码级的——`backend/moyu_context.py` 的 `assemble_context()`
    消费 `retrieval["results"]` 时没有任何相关性阈值（`relevance` 只被写进证据
    记录）。所以这里守住的是「这个观测量不能消失」，而不是它的符号。
    """
    count = results["books"][0]["retrieval"]["absent_phrase_result_count"]
    assert isinstance(count, int) and count >= 0, count
    assert count <= 20, f"返回条数超过 limit：{count}"


def main() -> int:
    test_repair_typo()
    test_normalize_rules()
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        corpus = test_prepare_end_to_end(tmp)
        results = test_run_end_to_end(tmp, corpus)
        test_absent_phrase_is_measured(results)
        summary = {
            "chapters": results["corpus"]["chapter_count"],
            "chars": results["corpus"]["total_chars"],
            "fidelity": "0 mismatch",
            "materials": results["books"][0]["retrieval"]["material_count"],
            "absent_phrase_results": results["books"][0]["retrieval"]["absent_phrase_result_count"],
        }
    print(f"real corpus fixtures: OK {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
