"""把公版长篇小说的逐回 HTML 归一化成干净的 Markdown 语料。

这个脚本只做一件事：把第三方镜像里的 HTML 变成**可复算的纯文本**，并记录
每个文件的 sha256。它不生成任何断言、不调用任何模型。

用法：
    python benchmarks/real_corpus/prepare.py \
        --source /tmp/moyu-real-corpus/china-literary-master \
        --book 红楼梦 --book 三国演义 --book 水浒传 \
        --out benchmarks/real_corpus/corpus

产物结构：
    <out>/<book>/001.md ... 120.md
    <out>/manifest.json

归一化规则（三条，全部可核对）：
1. 去掉站点导航：每回 HTML 顶部有「← 阿星星的首页 / 首页 / <书名>-原文版… / 下一节：…」
   这几行，正文从标题行（以「 原文」结尾）之后开始。
2. 去脂批：红楼梦的甲戌/庚辰/蒙府批语混在正文里，形如
   `` ```(甲戌侧批：…)``` ``。这些是后人评点，不是作者正文，会把字数和句子
   边界算错，必须剔除。
3. 折叠空白：全角空格变半角，连续空行折叠成一个。**不删改任何正文字符**。

为什么不把语料提交进仓库：三本书合计约 6MB 且是第三方镜像。仓库只放脚本和
manifest（含 sha256），任何人都能用同一个源复算出同样的语料。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path

CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}

# 脂批：`` ```(甲戌侧批：…)``` `` 是最常见形态；也兜底处理不加代码围栏的括号批语。
CRITICISM = re.compile(r"```\s*\([^)]*批[^)]*\)\s*```|```\s*\([^)]*\)\s*```")
CHAPTER_TITLE = re.compile(r"^(第[零〇一二三四五六七八九十百千两]+回)\s*(.*?)\s*原文\s*$")


def repair_typo(text: str) -> str:
    """修源站的回目笔误：「第一百二零回」应为「第一百二十回」。

    规则收窄到一种形态——数字后面紧跟「零/〇」且「零」到此结束——才当作
    「十」。这条规则只影响源站写错的个别回目，不改变标准写法（如
    「一百零一」里的零是占位符，不在词尾，不受影响）。
    """
    return re.sub(r"(?<=[一二三四五六七八九])[零〇]$", "十", text)


def cn_to_int(text: str) -> int:
    """把「一百十三」这类中文数字转成整数。只处理到千位，够用即可。"""
    text = repair_typo(text)
    total = 0
    section = 0
    number = 0
    for char in text:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
        elif char in CN_UNITS:
            unit = CN_UNITS[char]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        else:
            raise ValueError(f"不是中文数字：{char!r}")
    return total + section + number


def strip_tags(raw: str) -> str:
    """把 HTML 正文抽成纯文本，保留段落边界。"""
    match = re.search(r"<body[^>]*>(.*)</body>", raw, re.S | re.I)
    body = match.group(1) if match else raw
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.S | re.I)
    body = re.sub(r"<br\s*/?>|</p\s*>|</div\s*>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", "", body)
    return html.unescape(body)


def is_navigation(line: str) -> bool:
    """判断一行是不是站点导航/页脚，而不是正文。

    只匹配**整行**形态，且要求导航行自己带章节编号或站点特征，避免误删正文
    （实测《红楼梦》第 69 回正文里就有「察院便批：」「当堂批准」，那是叙事
    不是批注，所以任何按关键字「批」做的删除都必须限定形态）。
    """
    if not line:
        return False
    if line.startswith(("←", "→", "↑")):
        return True
    if line == "首页":
        return True
    if re.match(r"^[上下]一[节回][:：]\s*第[零〇一二三四五六七八九十百千两]+[回节]", line):
        return True
    if line.startswith("©"):
        return True
    if "阿星星" in line and len(line) <= 40:
        return True
    if line.endswith("：目录") and "原文版" in line:
        return True
    return False


def normalize(raw: str) -> tuple[str, str]:
    """返回 (回目标题, 正文)。导航行与脂批在这里被剔除。"""
    lines = [line.replace("\u3000", " ").strip() for line in strip_tags(raw).splitlines()]
    title = ""
    body: list[str] = []
    started = False
    for line in lines:
        if not started:
            match = CHAPTER_TITLE.match(line)
            if match:
                title = f"{match.group(1)} {match.group(2)}".strip()
                started = True
            continue
        if line and not is_navigation(line):
            body.append(line)
    if not started:
        raise ValueError("没有找到回目标题行（以「 原文」结尾）")
    text = "\n".join(body)
    text = CRITICISM.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text.strip()


def pick_source(directory: Path) -> Path:
    """定位「原文版<书名>」子目录。"""
    candidates = sorted(p for p in directory.iterdir() if p.is_dir() and p.name.startswith("原文版"))
    if not candidates:
        raise SystemExit(f"{directory} 下没有「原文版<书名>」目录")
    return candidates[0]


def prepare_book(source: Path, book: str, out: Path) -> dict:
    chapters_dir = pick_source(source / book)
    files = sorted(chapters_dir.glob("*.html"))
    if not files:
        raise SystemExit(f"{chapters_dir} 里没有 HTML")

    rows = []
    for path in files:
        match = re.match(r"^(第[零〇一二三四五六七八九十百千两]+回)", path.stem)
        if not match:
            raise SystemExit(f"文件名里没有回数：{path.name}")
        ordinal = cn_to_int(match.group(1)[1:-1])
        title, text = normalize(path.read_text(encoding="utf-8", errors="replace"))
        rows.append({"ordinal": ordinal, "title": title, "text": text, "source": path.name})
    rows.sort(key=lambda row: row["ordinal"])
    ordinals = [row["ordinal"] for row in rows]
    if ordinals != list(range(1, len(rows) + 1)):
        missing = sorted(set(range(1, max(ordinals) + 1)) - set(ordinals))
        raise SystemExit(f"{book} 回目不是连续 1..N，缺失 {missing[:10]}")

    target = out / book
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob("*.md"):
        stale.unlink()

    manifest = {"book": book, "chapters": [], "source": "hunterhug/china-literary@master"}
    total = 0
    for row in rows:
        name = f"{row['ordinal']:03d}.md"
        payload = f"# {row['title']}\n\n{row['text']}\n"
        data = payload.encode("utf-8")
        (target / name).write_bytes(data)
        chars = len(row["text"])
        total += chars
        manifest["chapters"].append({
            "ordinal": row["ordinal"],
            "file": f"{book}/{name}",
            "title": row["title"],
            "chars": chars,
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest["chapter_count"] = len(rows)
    manifest["total_chars"] = total
    print(f"{book}: {len(rows)} 回 / {total} 字")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把公版长篇归一化成可复算语料")
    parser.add_argument("--source", required=True, help="china-literary 解压后的根目录")
    parser.add_argument("--book", action="append", required=True, help="书名，可重复")
    parser.add_argument("--out", required=True, help="输出目录")
    args = parser.parse_args(argv)

    source = Path(args.source)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    books = [prepare_book(source, book, out) for book in args.book]
    manifest = {
        "corpus_version": 1,
        "license": "public-domain（作者逝世逾百年，正文无版权）",
        "books": books,
        "total_chars": sum(book["total_chars"] for book in books),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"合计 {manifest['total_chars']} 字 / {sum(b['chapter_count'] for b in books)} 回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
