"""从真实古籍正文里确定性抽取事实断言（无模型）。

为什么要有这个文件
------------------
`docs/consistency-baseline-2026-10-23.md` 的验收是「30 万字 × 3 部真实作品的
一致性检查」，一直卡在「需要 AI 把正文抽成 assertion」这一段，因为本机没有
provider 凭据。这个模块把那段换成**确定性规则**：规则抽得少，但抽出来的是真事
实，落库、采纳、引擎全部走生产路径——「抽取 → 采纳 → 引擎」因此是真跑通的，
只有抽取器不是 LLM。

精度优先，不做召回
------------------
在 234 万字上实测过每条规则的抽样（`docs/real-corpus-acceptance-2026-10-24.md`
§2 记了抽样样本）：

- `姓X名Y`（可带 `字Z`）：命中即真名，三国 100 条、水浒 141 条、红楼 5 条。
- `身长X尺`：命中即身高（`身长八尺四五` 这类也稳）。
- `年方X岁`：命中即年龄。
- `A字B`：**只在 A 已经是册内实体、且 B 不像普通名词时**才采用。裸用的精度
  很差——红楼的「知书识字」「闺阁字样」「十字街口」、水浒的「帅字旗」全会命中，
  所以这条规则单独用会把误报灌进库，必须加白名单门。

规则抽不到的（对话里的知识、伏笔、称谓变化）本模块**不猜**，全部留给 LLM 抽取。
所以这里的召回率没有意义，只报告抽出了多少条、以及引擎在这些真实断言上的表现。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

# 名词/虚词黑名单：一个字里出现这些，就不可能是人名用字。
# 这是精度闸门——宁可漏掉真名，也不要把「字样」「街口」当成人。
NOT_NAME_CHARS = set(
    "的了是在把被不没和与及或而且但却又也还都只才已曾要可能会得地着过"
    "我你他她它这那哪谁什怎吗呢吧啊么个只件条张片块团群队些每各另"
    "样号旗体辈句义音面纸典数眼儿母头心手口脚目身花草木山水火刀剑马车船门路店"
    "书文诗词画字姓名来去时日月年分尺寸上下左右前后东西南北中内外间"
    "大小多少长短高低新旧好坏人天地神鬼生死笑哭走跑坐立看听说问答叫"
    "一二三四五六七八九十百千万两几半"
)

SENTENCE_SPLIT = re.compile(r"[。！？；\n]+")
RE_SURNAME_GIVEN = re.compile(r"姓([\u4e00-\u9fa5])名([\u4e00-\u9fa5]{1,2})(?:字([\u4e00-\u9fa5]{1,2}))?")
RE_COURTESY = re.compile(r"([\u4e00-\u9fa5]{2,3})字([\u4e00-\u9fa5]{1,2})(?=[，,。、；：\s]|$)")
RE_HEIGHT = re.compile(r"身长([一二三四五六七八九十]{1,3}尺(?:[一二三四五六七八九十]分)?(?:有零)?)")
RE_AGE = re.compile(r"年方([一二三四五六七八九十]{1,3})岁")
RE_ALIAS = re.compile(r"([\u4e00-\u9fa5]{2,3})(?:的)?(?:绰号|浑名|外号|别号)(?:叫做|唤作|叫)?([\u4e00-\u9fa5]{2,4})")
RE_NATIVE = re.compile(r"([\u4e00-\u9fa5]{2,5})(?:人氏|人也)")
# 地名后缀字：只收「X郡/X县/X州」这类明确形态，挡住「乃本地人也」的误抽。
PLACE_SUFFIX = set("郡县州府城镇乡村亭关寨卫京国阳阴东西南北陵邑郭")
FUNCTION_CHARS = set("的了是在把被不没和与及或而且但却又也还都只才已曾要可能会我你他她它这那")
RE_CHILD = re.compile(r"([\u4e00-\u9fa5]{2,3})之子([\u4e00-\u9fa5]{2,3})")
RE_SIBLING = re.compile(r"([\u4e00-\u9fa5]{2,3})之(弟|兄)([\u4e00-\u9fa5]{2,3})")
RE_NICKNAME = re.compile(r"(?:绰号|浑名|外号|人称|号曰)(?:叫做|唤作|叫|为)?([\u4e00-\u9fa5]{2,4})")


def looks_like_name(text: str) -> bool:
    """人名用字闸门：长度 2–3、无虚词/名词用字、不自带数字。"""
    if not (2 <= len(text) <= 3):
        return False
    return not any(char in NOT_NAME_CHARS for char in text)


@dataclass(frozen=True)
class Fact:
    """一条抽取结果。`quote` 是要写进 evidence 的原文凭据。"""

    book: str
    chapter_ordinal: int
    subject: str
    predicate: str
    attribute: str | None
    value: str
    quote: str

    @property
    def object_json(self) -> str:
        payload: dict[str, Any] = {"value": self.value}
        if self.attribute:
            payload["attribute"] = self.attribute
            payload["name"] = self.subject
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def iter_sentences(text: str) -> Iterator[str]:
    for raw in SENTENCE_SPLIT.split(text):
        line = raw.strip()
        if line:
            yield line


def read_book(corpus: Path, book: str) -> list[tuple[int, str]]:
    chapters = []
    for path in sorted((corpus / book).glob("*.md")):
        ordinal = int(path.stem.split(".")[0])
        payload = path.read_text(encoding="utf-8")
        lines = payload.split("\n", 2)
        body = lines[2] if len(lines) > 2 else ""
        chapters.append((ordinal, body))
    return chapters


def build_roster(chapters: Iterable[tuple[int, str]], min_hits: int = 3) -> dict[str, str]:
    """先用高精度规则建实体名册：`姓X名Y` 一次即可入册，`X曰/道` 要够次数。"""
    roster: dict[str, str] = {}
    speech: dict[str, int] = {}
    for _ordinal, body in chapters:
        for match in RE_SURNAME_GIVEN.finditer(body):
            name = match.group(1) + match.group(2)
            if looks_like_name(name):
                roster.setdefault(name, name)
        for match in re.finditer(r"([\u4e00-\u9fa5]{2,3})(?:曰|道|说道|回道|答道)[：:，,]", body):
            candidate = match.group(1)
            if looks_like_name(candidate):
                speech[candidate] = speech.get(candidate, 0) + 1
    for name, hits in speech.items():
        if hits >= min_hits:
            roster.setdefault(name, name)
    return roster


def link_subject(sentence: str, roster: dict[str, str]) -> str | None:
    """一句里只认**恰好一个**册内实体；认错了比漏掉更贵，所以有歧义就放弃。"""
    found = [name for name in roster if name in sentence]
    if len(found) != 1:
        return None
    return found[0]


def extract_book(book: str, chapters: list[tuple[int, str]], strict: bool = True) -> list[Fact]:
    """`strict=True` 只保留**不需要句内实体链接**的规则。

    为什么必须这么分：`身长八尺` 要知道这八尺说的是谁。没有共指消解时，
    「玄德视其人，身长八尺」会把玄德判成八尺——实测这类错挂把 native_place /
    height 抽出大量垃圾值（「乃本地」「这些娘家的」），库里随即出现大量「矛盾」。
    那些矛盾不是引擎误报，是**脏数据被正确指出**；但拿脏数据量引擎精度没有意义。
    所以 strict 只留主语自带名字的规则：`姓X名Y`、`X字Y`（X 须在册）、
    `A之子B` / `A之弟B`（两侧都须在册）。
    """
    roster = build_roster(chapters)
    facts: list[Fact] = []
    for ordinal, body in chapters:
        for sentence in iter_sentences(body):
            for match in RE_SURNAME_GIVEN.finditer(sentence):
                name = match.group(1) + match.group(2)
                if not looks_like_name(name):
                    continue
                facts.append(Fact(book, ordinal, name, "has_name", None, match.group(1) + match.group(2), sentence))
                courtesy = match.group(3)
                if courtesy and looks_like_name(courtesy):
                    facts.append(
                        Fact(book, ordinal, name, "has_courtesy_name", "courtesy_name", courtesy, sentence)
                    )
            for match in RE_COURTESY.finditer(sentence):
                name, courtesy = match.group(1), match.group(2)
                if not looks_like_name(name) or not looks_like_name(courtesy):
                    continue
                if name not in roster:
                    continue  # 关键闸门：名字不在册就不认，挡住「闺阁字样」
                facts.append(
                    Fact(book, ordinal, name, "has_courtesy_name", "courtesy_name", courtesy, sentence)
                )
            if strict:
                continue  # 以下规则都需要句内实体链接，strict 模式不启用
            for match in RE_HEIGHT.finditer(sentence):
                subject = link_subject(sentence, roster)
                if subject:
                    facts.append(Fact(book, ordinal, subject, "has_attribute", "height", match.group(1), sentence))
            for match in RE_AGE.finditer(sentence):
                subject = link_subject(sentence, roster)
                if subject:
                    facts.append(Fact(book, ordinal, subject, "has_attribute", "age", match.group(1) + "岁", sentence))
            # 籍贯：`东莱牟平人也`。地名字必须带后缀字，且不含虚词。
            for match in RE_NATIVE.finditer(sentence):
                place = match.group(1)
                if looks_like_name(place):
                    continue
                if not (set(place) & PLACE_SUFFIX) or (set(place) & FUNCTION_CHARS):
                    continue
                subject = link_subject(sentence, roster)
                if subject:
                    facts.append(Fact(book, ordinal, subject, "located_in", "native_place", place, sentence))
            # 父子 / 兄弟：两侧都必须在册，否则宁可不抽。
            for match in RE_CHILD.finditer(sentence):
                father, child = match.group(1), match.group(2)
                if father in roster and child in roster:
                    facts.append(Fact(book, ordinal, child, "has_parent", "father", father, sentence))
            for match in RE_SIBLING.finditer(sentence):
                elder, kind, younger = match.group(1), match.group(2), match.group(3)
                if elder in roster and younger in roster:
                    attribute = "elder_brother" if kind == "弟" else "younger_brother"
                    facts.append(Fact(book, ordinal, younger, "has_sibling", attribute, elder, sentence))
            # 绰号：必须有主语可挂，否则这条断言无处落地。
            for match in RE_NICKNAME.finditer(sentence):
                nickname = match.group(1)
                if not looks_like_name(nickname):
                    continue
                subject = link_subject(sentence, roster)
                if subject and nickname != subject:
                    facts.append(Fact(book, ordinal, subject, "has_courtesy_name", "nickname", nickname, sentence))
    return facts


def extract(corpus: Path, books: list[str], strict: bool = True) -> dict[str, Any]:
    all_facts: list[Fact] = []
    per_book: list[dict[str, Any]] = []
    for book in books:
        chapters = read_book(corpus, book)
        roster = build_roster(chapters)
        facts = extract_book(book, chapters, strict=strict)
        per_book.append(
            {
                "book": book,
                "chapters": len(chapters),
                "chars": sum(len(body) for _o, body in chapters),
                "roster": len(roster),
                "facts": len(facts),
                "unique_subjects": len({fact.subject for fact in facts}),
            }
        )
        all_facts.extend(facts)
    return {"books": per_book, "facts": all_facts}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="从真实古籍确定性抽取事实断言")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--book", action="append", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    result = extract(Path(args.corpus), args.book)
    facts = result["facts"]
    payload = {
        "books": result["books"],
        "facts": [
            {
                "book": f.book,
                "chapter_ordinal": f.chapter_ordinal,
                "subject": f.subject,
                "predicate": f.predicate,
                "attribute": f.attribute,
                "value": f.value,
                "quote": f.quote,
            }
            for f in facts
        ],
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in result["books"]:
        print(f"{row['book']}: {row['chapters']} 回 / {row['chars']} 字 / 名册 {row['roster']} / 断言 {row['facts']}")
    print(f"合计 {len(facts)} 条断言")
