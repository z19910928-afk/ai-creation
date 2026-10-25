"""墨屿一致性引擎：对故事状态投影做确定性矛盾检测。

设计约束
--------
- **只读**：不写数据库，不改任何 projection；所有结论都带 evidence id，可回到原文。
- **确定性**：同样的数据必须得到同样的 finding id 与分数，便于回归与对外发布基准。
- **可解释**：每条 finding 都带 category / severity / detail / evidence / suggestion。
- **可降级**：缺少某张表或某一列时跳过对应检测，而不是让整个分析崩溃。

六类矛盾对应 ``CATEGORY_LABELS`` 的前六项；``evidence`` 是额外的来源失效检查，
服务于"每句话有来路"的产品承诺，单独计分但不混入六类统计。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ENGINE_VERSION = 1

class ConsistencyScopeError(ValueError):
    """请求的项目或分支不存在。HTTP 层应映射为 404，而不是 400/500。"""


SEVERITY_WEIGHTS = {"blocker": 20.0, "high": 8.0, "medium": 3.0, "low": 1.0}
SEVERITY_ORDER = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_LABELS = {"blocker": "阻断", "high": "高", "medium": "中", "low": "低"}

CATEGORY_LABELS = {
    "fact": "事实矛盾",
    "timeline": "时间线矛盾",
    "knowledge": "知识越界",
    "attribute": "属性漂移",
    "relation": "关系/称谓矛盾",
    "foreshadow": "伏笔断线",
    "evidence": "来源失效",
}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_GRADE_THRESHOLDS = ((90.0, "A"), (75.0, "B"), (60.0, "C"))


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _parse_object_json(raw: Any) -> tuple[Any, bool]:
    """解析断言取值。返回 (值, 是否解析失败)。

    NULL / 空串按空对象处理（历史上确实存在），只有「非空但解析不了」才算数据损坏。
    """
    if raw is None or raw == "":
        return {}, False
    if isinstance(raw, (dict, list)):
        return raw, False
    try:
        return json.loads(raw), False
    except (TypeError, ValueError):
        return None, True


def _canonical(value: Any) -> str:
    """生成断言取值的规范化文本：用于分桶和展示，**不是**「语义等价」判定。

    这个键来自 `json.dumps(..., sort_keys=True)`，只抹平 JSON 文本层面的差异
    （空白、键顺序），比 `_same_value()` 严格：数值 `1` 与 `1.0` 的文本分别是
    `1` 与 `1.0`，会落进**不同的桶**。

    分桶键与语义等价是两个层次，不能互相替代：
    - `_canonical` 只决定「哪两条断言需要比一次」；跨桶不等于取值冲突，只说明
      这一对没有被分桶提前排除。
    - `_same_value()` 才是语义判定，它把 `1` 与 `1.0` 归一成同一个取值。

    因此 `1` 与 `1.0` 这类跨桶对只会**多走一次比较**，不会直接产生冲突：
    `_detect_fact()` 枚举跨桶配对，而 `_classify_object_conflict()` 第一步就是
    `_same_value(left, right)`，命中即判相同、返回 None。反过来也一样——不要把
    本函数的输出当成「两条断言是否矛盾」的依据。
    """
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _normalize_for_compare(value: Any) -> Any:
    """把取值归一成可比较的结构，用于判断「两条断言是否同一个取值」。

    与 `_canonical` 分开：`_canonical` 还要负责展示，不能把 1 显示成 1.0。
    这里只解决比较语义：
    - 数字统一成 float（`1` 与 `1.0` 是同一个取值）；
    - 布尔先于数字判断（Python 里 `True == 1`，但设定里「是」和「1」不是一回事）；
    - dict 按键排序，list 保持顺序（数组顺序有语义）。
    """
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        # 整数保持整数：转 float 会在 2**53 之后丢精度，超大整数还会 OverflowError。
        return ("number", value)
    if isinstance(value, float):
        if value != value:  # NaN
            return ("number", "nan")
        if value == float("inf") or value == float("-inf"):
            return ("number", "inf" if value > 0 else "-inf")
        # 1.0 与 1 是同一个取值；其余小数按原值比较。
        return ("number", int(value) if value.is_integer() else value)
    if isinstance(value, dict):
        return ("dict", tuple(sorted((str(k), _normalize_for_compare(v)) for k, v in value.items())))
    if isinstance(value, (list, tuple)):
        return ("list", tuple(_normalize_for_compare(item) for item in value))
    if value is None:
        return ("null", None)
    return ("str", str(value))


def _same_value(left: Any, right: Any) -> bool:
    """比较两个断言取值在语义上是否相同（忽略 JSON 空白、键顺序、1 与 1.0）。"""
    return _normalize_for_compare(left) == _normalize_for_compare(right)


def _rows(conn: sqlite3.Connection, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, tuple(parameters)).fetchall()
    finally:
        conn.row_factory = previous


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _iso(value: Any) -> bool:
    return bool(value) and bool(_ISO_DATE.match(str(value)))


def _overlap(a_start: Any, a_end: Any, b_start: Any, b_end: Any) -> bool:
    """判断两个有效区间是否重叠；无法解析的边界一律视为重叠（保守）。"""
    if a_start and b_end and _iso(a_start) and _iso(b_end) and str(a_start) > str(b_end):
        return False
    if b_start and a_end and _iso(b_start) and _iso(a_end) and str(b_start) > str(a_end):
        return False
    return True


def _finding(
    category: str,
    severity: str,
    title: str,
    detail: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    suggestion: str = "",
    key: str = "",
) -> dict[str, Any]:
    digest = hashlib.sha1(
        f"{category}|{severity}|{title}|{key}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "id": f"cx_{digest}",
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, category),
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": dict(evidence or {}),
        "suggestion": suggestion,
    }


def _chapter_ref(chapters: Mapping[str, Mapping[str, Any]], chapter_id: Any) -> str:
    if not chapter_id:
        return "未绑定章节"
    chapter = chapters.get(str(chapter_id))
    if not chapter:
        return f"章节 {chapter_id}（已不存在）"
    return f"第{chapter.get('ordinal')}章《{chapter.get('title')}》"


# --------------------------------------------------------------------------- #
# 检测上下文
# --------------------------------------------------------------------------- #
def _build_context(conn: sqlite3.Connection, project_id: str, branch_id: str) -> dict[str, Any]:
    entities: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "entity"):
        for row in _rows(
            conn,
            "SELECT id,canonical_name,kind,status,aliases_json FROM entity WHERE project_id=?",
            (project_id,),
        ):
            entities[row["id"]] = {
                "id": row["id"],
                "name": row["canonical_name"],
                "kind": row["kind"],
                "status": row["status"],
                "aliases": _loads(row["aliases_json"], []),
            }

    chapters: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "document_node"):
        for row in _rows(
            conn,
            "SELECT id,title,ordinal FROM document_node "
            "WHERE project_id=? AND branch_id=? AND kind='chapter'",
            (project_id, branch_id),
        ):
            chapters[row["id"]] = {"id": row["id"], "title": row["title"], "ordinal": row["ordinal"]}

    latest_revision: dict[str, str] = {}
    if _table_exists(conn, "document_revision"):
        for row in _rows(
            conn, "SELECT document_node_id,id FROM document_revision ORDER BY rowid"
        ):
            latest_revision[row["document_node_id"]] = row["id"]

    deleted_chapters: set[str] = set()
    if _table_exists(conn, "app_deleted_node"):
        deleted_chapters = {
            row["chapter_id"] for row in _rows(conn, "SELECT chapter_id FROM app_deleted_node")
        }

    # 被删除的章节不推进故事时间线，否则「当前写到第几章」会被幽灵章节撑大，
    # 让所有伏笔都误判为超期未回收。
    max_ordinal = max(
        (item["ordinal"] for cid, item in chapters.items() if cid not in deleted_chapters),
        default=0,
    )
    return {
        "entities": entities,
        "chapters": chapters,
        "latest_revision": latest_revision,
        "deleted_chapters": deleted_chapters,
        "max_ordinal": max_ordinal,
    }


# --------------------------------------------------------------------------- #
# 1. 事实矛盾
# --------------------------------------------------------------------------- #
def _attribute_slot(value: Any) -> str:
    """取断言对象的属性槽位名（`object.attribute`）；没有属性维度的返回空串。

    空串是一个真实槽位（「未分类事实」），表示这条断言没有声明自己属于哪个属性，
    只能按 `name` 参与身份漂移判断，不再参与「同一事实的两种描述」这类判断。
    """
    if not isinstance(value, dict):
        return ""
    raw = value.get("attribute")
    if not isinstance(raw, str):
        return ""
    return raw.strip()


# 带槽位语义的谓词：它们的取值按「属性」组织（某个角色/物品的某一项属性）。
# 这张表只回答「这个谓词本不本该带 attribute 槽位」，不参与「怎么比」——
# 怎么比仍然看数据形状（`_attribute_slot`）。两者必须分开：
# 历史库里存在没有 attribute 的 `has_attribute`（写入端 t41 之前放行过），
# 这些行只有形状没有槽位，需要靠「谓词带槽位语义」这个知识才能判成「不比」。
_SLOT_SEMANTIC_PREDICATES = frozenset({"has_attribute", "has_story_fact"})


def _has_slot_semantics(predicate: str) -> bool:
    """该谓词是否按属性槽位组织取值（`has_attribute` / `has_story_fact`）。"""
    return str(predicate or "").strip() in _SLOT_SEMANTIC_PREDICATES


def _classify_object_conflict(
    predicate: str, left: Any, right: Any
) -> tuple[str | None, str, str]:
    """返回 (severity, title, detail)；None 表示不构成矛盾。

    判断依据是**数据形状 + 谓词是否带槽位语义**，不是谓词名到严重度的硬编码表：
    - 只要断言对象里带 `attribute` 槽位，就按「同一槽位才可能是同一件事」处理
      （`has_attribute`、`has_story_fact`、将来新增的带槽位谓词全部适用）；
    - 谓词本身带槽位语义、但两侧都没有槽位时（历史行），**跳过配对**：

      `has_attribute` 的语义是「某条属性的取值」，没有 `attribute` 就无法确定
      两条断言在比哪一条属性——「左臂有疤」与「惯用右手」是两条不同属性的事实，
      按 note 判 high 就是把 N 条属性事实两两当矛盾的假阳。写入端（t41）已经不再
      产生这种行，但库里可能还有历史行，所以引擎侧必须兜住。
    - 只有不带槽位的单值谓词（`located_in`、`age`、`owns_item` 等）才保留
      「同一时段两个取值即冲突」。

    `has_story_fact` 的历史语义（t24 修订）：同一个实体身上挂着多条**不同**事实
    是常态，不是矛盾。「note 描述不同」已从判定里删除——它是旧版把 N 条事实
    两两当矛盾（N² 假阳）的直接来源。仍然保留两类真冲突：
    - 名字漂移：同一实体被记成两个名字（身份可能已经变了）；
    - 属性冲突：两条断言落在**同一个** attribute 槽位，但 value 取值不同。
    """
    if _same_value(left, right):
        return None, "", ""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return (
            "high",
            f"{predicate} 存在两个不同取值",
            f"同一主语在重叠有效期内被断言为 {_canonical(left)} 与 {_canonical(right)}。",
        )
    left_attribute = _attribute_slot(left)
    right_attribute = _attribute_slot(right)
    if left_attribute or right_attribute:
        if not (left_attribute and left_attribute == right_attribute):
            # 不同属性槽位：两条断言讲的是两件事，天生不需要比较。
            # 依据是数据形状而不是谓词名，所以 t23 的 has_attribute 与未来的带槽位谓词都覆盖。
            # 一侧有槽位、另一侧没有也算「不同槽位」：后者没声明自己在讲哪条属性，
            # 无从确认是同一件事，报冲突同样是假阳。
            return None, "", ""
        if "value" in left or "value" in right:
            left_value = left["value"] if "value" in left else "（未记录取值）"
            right_value = right["value"] if "value" in right else "（未记录取值）"
            if not _same_value(left_value, right_value):
                return (
                    "high",
                    f"属性「{left_attribute}」存在两个不同取值",
                    f"属性「{left_attribute}」在重叠有效期内被记为 "
                    f"{_canonical(left_value)} 与 {_canonical(right_value)}。",
                )
        # 同一槽位且取值相同（或双方都没记录取值）：不算属性冲突，继续看名字漂移。
    elif _has_slot_semantics(predicate):
        # 带槽位语义但两侧都没槽位：无法确定在比哪一条属性，跳过配对（只保留名字漂移判断）。
        pass
    else:
        # 不带属性槽位的单值谓词：同一谓词在同一时段出现两个取值就是冲突。
        return (
            "high",
            f"{predicate} 存在冲突取值",
            f"结构化断言取值不一致：{_canonical(left)} vs {_canonical(right)}。",
        )
    left_name = str(left.get("name") or "").strip()
    right_name = str(right.get("name") or "").strip()
    if left_name and right_name and left_name != right_name:
        return (
            "high",
            "同一实体被记成两个名字",
            f"同一实体在重叠有效期内分别被记作「{left_name}」与「{right_name}」，身份可能已经漂移。",
        )
    return None, "", ""


def _detect_fact(conn, project_id, branch_id, ctx) -> tuple[list[dict], int]:
    if not _table_exists(conn, "assertion") or not _table_exists(conn, "entity"):
        return [], 0
    rows = _rows(
        conn,
        """SELECT a.id,a.subject_entity_id,a.predicate,a.object_json,a.epistemic_mode,
                  a.valid_start,a.valid_end,a.supersedes_id,e.canonical_name
             FROM assertion a JOIN entity e ON e.id=a.subject_entity_id
            WHERE a.project_id=? AND a.branch_id=? AND a.state='canon'
            ORDER BY a.subject_entity_id,a.predicate,a.valid_start,a.id""",
        (project_id, branch_id),
    )
    findings: list[dict] = []
    # 先单独扫一遍坏 JSON：每条断言只报一次，且不参与后续配对。
    # 否则「单条坏断言」会因为凑不出配对而静默漏报，「坏 + 合法」会按配对数重复报。
    parsed: dict[str, tuple[Any, bool]] = {}
    for row in rows:
        value, broken = _parse_object_json(row["object_json"])
        parsed[row["id"]] = (value, broken)
        if broken:
            findings.append(
                _finding(
                    "fact",
                    "medium",
                    "断言取值不是合法 JSON",
                    f"断言 {row['id']} 的 object_json 无法解析为结构化取值，"
                    "这条事实目前无法参与一致性比对。",
                    evidence={"assertion_id": row["id"], "object_json": row["object_json"]},
                    suggestion="修正 object_json，或把该断言降级为 candidate 后重录。",
                    key=f"{row['id']}:broken",
                )
            )
    # 分组键必须带属性槽位：`天性怕水` 与 `欠船老大三块钱` 属于不同属性，
    # 天然不需要互相比较；只有同 (实体, 谓词, 属性) 的断言才可能是同一件事的两种取值。
    # 坏 JSON 单独成组（它们已各报一条，不再参与配对）。
    groups: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in rows:
        value, broken = parsed[row["id"]]
        slot = "" if broken else _attribute_slot(value)
        groups.setdefault((row["subject_entity_id"], row["predicate"], slot), []).append(row)

    for (entity_id, predicate, attribute), items in sorted(groups.items()):
        entity_name = items[0]['canonical_name']
        position = {row['id']: index for index, row in enumerate(items)}
        # 按规范化取值分桶，只枚举**跨桶**配对。
        # 同一取值的一对按定义不可能构成取值冲突（_same_value 原本就直接 continue），
        # 所以跨桶枚举的输出与逐对比较逐条一致，但复杂度从「组内条数²」降到
        # 「桶大小之积」——逐章重复抽出的同值断言不再把耗时放大成平方。
        buckets: dict[str, list] = {}
        for row in items:
            value, broken = parsed[row["id"]]
            if broken:
                continue
            buckets.setdefault(_canonical(value), []).append(row)
        bucket_list = list(buckets.values())
        for first in range(len(bucket_list)):
            for second in range(first + 1, len(bucket_list)):
                for left_row in bucket_list[first]:
                    for right_row in bucket_list[second]:
                        # 用原始位置还原左右顺序，保证 finding 的 key 与旧实现完全一致。
                        if position[left_row["id"]] < position[right_row["id"]]:
                            left, right = left_row, right_row
                        else:
                            left, right = right_row, left_row
                        left_object, left_broken = parsed[left["id"]]
                        right_object, right_broken = parsed[right["id"]]
                        if left_broken or right_broken:
                            continue
                        # 取值相同的一对已在分桶时排除；跨桶即「规范化后取值不同」，
                        # 不再需要逐对做 _same_value（这正是原来 O(n²) 的主要成本）。
                        if left["supersedes_id"] == right["id"] or right["supersedes_id"] == left["id"]:
                            continue
                        if not _overlap(
                            left["valid_start"], left["valid_end"], right["valid_start"], right["valid_end"]
                        ):
                            continue
                        if "author_plan" in (left["epistemic_mode"], right["epistemic_mode"]):
                            continue
                        severity, title, detail = _classify_object_conflict(
                            predicate, left_object, right_object
                        )
                        if severity is None:
                            continue
                        findings.append(
                            _finding(
                                "fact",
                                severity,
                                title,
                                f"{entity_name}：{detail}",
                                evidence={
                                    "entity_id": entity_id,
                                    "assertion_ids": [left["id"], right["id"]],
                                    "attribute": attribute or None,
                                    "objects": [_loads(left["object_json"], {}), _loads(right["object_json"], {})],
                                },
                                suggestion="保留一条为 canon，另一条改为 superseded 或补充 valid_start/valid_end 区分时段。",
                                key=f"{left['id']}|{right['id']}",
                            )
                        )
    return findings, len(rows)


# --------------------------------------------------------------------------- #
# 2. 时间线矛盾
# --------------------------------------------------------------------------- #
def _detect_timeline(conn, project_id, branch_id, ctx) -> tuple[list[dict], int]:
    findings: list[dict] = []
    examined = 0

    if _table_exists(conn, "app_timeline_event"):
        rows = _rows(
            conn,
            "SELECT id,title,event_time,chapter_id,status,ordinal FROM app_timeline_event "
            "WHERE project_id=? AND branch_id=? ORDER BY ordinal,created_at",
            (project_id, branch_id),
        )
        examined += len(rows)
        by_ordinal: dict[int, list[sqlite3.Row]] = {}
        by_chapter: dict[str, set[str]] = {}
        for row in rows:
            if str(row["status"]) in {"abandoned", "cancelled"}:
                continue
            if row["chapter_id"] and row["chapter_id"] in ctx["deleted_chapters"]:
                findings.append(
                    _finding(
                        "timeline",
                        "high",
                        "时间线事件挂在已删除章节上",
                        f"事件「{row['title']}」引用章节 {row['chapter_id']}，但该章节已被删除。",
                        evidence={"event_id": row["id"], "chapter_id": row["chapter_id"]},
                        suggestion="重新绑定到现存章节，或把事件标记为 abandoned。",
                        key=row["id"],
                    )
                )
            try:
                ordinal = int(row["ordinal"])
            except (TypeError, ValueError):
                findings.append(
                    _finding(
                        "timeline",
                        "medium",
                        "时间线事件的序位不是整数",
                        f"事件「{row['title']}」的 ordinal 为 {row['ordinal']!r}，无法参与时序核对。",
                        evidence={"event_id": row["id"], "ordinal": row["ordinal"]},
                        suggestion="把 ordinal 修正为整数。",
                        key=f"{row['id']}:ordinal",
                    )
                )
                ordinal = None
            if ordinal is not None:
                by_ordinal.setdefault(ordinal, []).append(row)
            if row["chapter_id"] and row["event_time"]:
                by_chapter.setdefault(row["chapter_id"], set()).add(str(row["event_time"]))

        for ordinal, items in sorted(by_ordinal.items()):
            chapters = {str(item["chapter_id"]) for item in items if item["chapter_id"]}
            if len(chapters) > 1:
                findings.append(
                    _finding(
                        "timeline",
                        "medium",
                        "同一序位挂着多个章节的事件",
                        f"序位 {ordinal} 上同时存在 {len(chapters)} 个不同章节的事件："
                        + "、".join(_chapter_ref(ctx["chapters"], cid) for cid in sorted(chapters)),
                        evidence={"ordinal": ordinal, "event_ids": [item["id"] for item in items]},
                        suggestion="给每个事件一个唯一 ordinal，或明确它们同属一个场景。",
                        key=f"ordinal:{ordinal}",
                    )
                )

        for chapter_id, times in sorted(by_chapter.items()):
            if len(times) > 1:
                findings.append(
                    _finding(
                        "timeline",
                        "medium",
                        "同一章节出现多个故事时间",
                        f"{_chapter_ref(ctx['chapters'], chapter_id)} 同时标注了 "
                        + "、".join(sorted(times))
                        + "，章节内部时间可能断裂。",
                        evidence={"chapter_id": chapter_id, "times": sorted(times)},
                        suggestion="确认章节内部的场景切换点，或合并为同一个故事时间。",
                        key=f"chapter-time:{chapter_id}",
                    )
                )

    if _table_exists(conn, "assertion"):
        rows = _rows(
            conn,
            "SELECT id,subject_entity_id,predicate,valid_start,valid_end FROM assertion "
            "WHERE project_id=? AND branch_id=? AND state='canon' "
            "AND valid_start IS NOT NULL AND valid_end IS NOT NULL",
            (project_id, branch_id),
        )
        examined += len(rows)
        for row in rows:
            if _iso(row["valid_start"]) and _iso(row["valid_end"]) and str(row["valid_start"]) > str(row["valid_end"]):
                findings.append(
                    _finding(
                        "timeline",
                        "high",
                        "事实的有效期起点晚于终点",
                        f"断言 {row['id']}（{row['predicate']}）的 valid_start={row['valid_start']} "
                        f"晚于 valid_end={row['valid_end']}，这条事实永远不会生效。",
                        evidence={"assertion_id": row["id"]},
                        suggestion="修正有效期，或把这条断言标记为 retracted。",
                        key=row["id"],
                    )
                )
    return findings, examined


# --------------------------------------------------------------------------- #
# 3. 知识越界
# --------------------------------------------------------------------------- #
def _known_by_of(payload: Any) -> list[str]:
    data = _loads(payload, {})
    if not isinstance(data, dict):
        return []
    values = data.get("known_by")
    if isinstance(values, list):
        return [str(item) for item in values if str(item).strip()]
    scope = data.get("knowledge_scope")
    if isinstance(scope, dict) and isinstance(scope.get("known_by"), list):
        return [str(item) for item in scope["known_by"] if str(item).strip()]
    return []


def _detect_knowledge(conn, project_id, branch_id, ctx) -> tuple[list[dict], int]:
    if not _table_exists(conn, "assertion") or not _table_exists(conn, "entity"):
        return [], 0
    has_memory = _table_exists(conn, "candidate_memory")
    memory_join = (
        """LEFT JOIN candidate_memory cm ON cm.id = (
               SELECT json_extract(e.value,'$.candidate_memory_id')
                 FROM json_each(a.evidence_json) e
                WHERE e.type='object'
                  AND json_extract(e.value,'$.candidate_memory_id') IS NOT NULL
                LIMIT 1)"""
        if has_memory
        else "LEFT JOIN (SELECT NULL id,NULL payload_json,NULL status) cm ON 0"
    )
    rows = _rows(
        conn,
        f"""SELECT a.id,a.subject_entity_id,a.predicate,a.epistemic_mode,a.object_json,
                   a.evidence_json,cm.id memory_id,cm.payload_json,cm.status memory_status,
                   e.canonical_name,e.kind entity_kind
              FROM assertion a JOIN entity e ON e.id=a.subject_entity_id
              {memory_join}
             WHERE a.project_id=? AND a.branch_id=? AND a.state='canon'""",
        (project_id, branch_id),
    )
    findings: list[dict] = []
    for row in rows:
        entity = ctx["entities"].get(row["subject_entity_id"], {})
        entity_name = entity.get("name") or row["canonical_name"]
        known_by = _known_by_of(row["payload_json"]) if row["memory_id"] else []
        if not known_by:
            continue
        if row["epistemic_mode"] == "character_belief":
            if entity_name not in known_by and row["subject_entity_id"] not in known_by:
                findings.append(
                    _finding(
                        "knowledge",
                        "high",
                        "角色相信自己不知道的事",
                        f"{entity_name} 被记为相信「{row['predicate']}」，但来源记忆的 known_by 只有 "
                        + "、".join(known_by)
                        + "。",
                        evidence={
                            "assertion_id": row["id"],
                            "memory_id": row["memory_id"],
                            "entity_id": row["subject_entity_id"],
                            "known_by": known_by,
                        },
                        suggestion="把这条断言改为 world_truth，或把该角色加入 known_by 并说明获知章节。",
                        key=row["id"],
                    )
                )
        elif row["epistemic_mode"] == "world_truth":
            private = _loads(row["payload_json"], {})
            # AI 生成的 payload 可能把 knowledge_scope 写成字符串；畸形数据只应
            # 影响这一条判定，不能让整份报告 500（它还会传导到发表导出）。
            scope = private.get("knowledge_scope") if isinstance(private, dict) else None
            scope = scope if isinstance(scope, dict) else {}
            is_private = bool(isinstance(private, dict) and (private.get("private") or scope.get("private")))
            if is_private:
                findings.append(
                    _finding(
                        "knowledge",
                        "medium",
                        "局部认知被提升为世界事实",
                        f"来源记忆只对 {'、'.join(known_by)} 可见，但断言 {row['id']} 以 world_truth 进入 canon。",
                        evidence={"assertion_id": row["id"], "memory_id": row["memory_id"], "known_by": known_by},
                        suggestion="改回 character_belief，或补充「所有人都已知晓」的章节证据后再提升。",
                        key=row["id"],
                    )
                )
        if row["epistemic_mode"] == "character_belief" and entity.get("kind") not in {"character", "人物", None, ""}:
            findings.append(
                _finding(
                    "knowledge",
                    "low",
                    "非人物实体被标记为持有信念",
                    f"{entity_name} 的类型是 {entity.get('kind')}，却以 character_belief 记录。",
                    evidence={"assertion_id": row["id"], "entity_id": row["subject_entity_id"]},
                    suggestion="确认该实体的类型，或改用 world_truth / inference。",
                    key=row["id"],
                )
            )
    return findings, len(rows)


# --------------------------------------------------------------------------- #
# 4. 属性漂移
# --------------------------------------------------------------------------- #
def _detect_attribute(conn, project_id, branch_id, ctx) -> tuple[list[dict], int]:
    if not _table_exists(conn, "character_state") or not _table_exists(conn, "entity"):
        return [], 0
    rows = _rows(
        conn,
        """SELECT cs.id,cs.entity_id,cs.attribute,cs.value_json,cs.valid_start,cs.valid_end,
                  cs.source_assertion_id,e.canonical_name
             FROM character_state cs JOIN entity e ON e.id=cs.entity_id
            WHERE cs.project_id=? AND cs.branch_id=?
            ORDER BY cs.entity_id,cs.attribute,cs.valid_start,cs.id""",
        (project_id, branch_id),
    )
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault((row["entity_id"], row["attribute"]), []).append(row)

    findings: list[dict] = []
    for (entity_id, attribute), items in sorted(groups.items()):
        entity_name = items[0]["canonical_name"]
        for index in range(len(items)):
            for other in range(index + 1, len(items)):
                left, right = items[index], items[other]
                if left["value_json"] == right["value_json"]:
                    continue
                if left["source_assertion_id"] and left["source_assertion_id"] == right["source_assertion_id"]:
                    continue
                if not _overlap(
                    left["valid_start"], left["valid_end"], right["valid_start"], right["valid_end"]
                ):
                    continue
                findings.append(
                    _finding(
                        "attribute",
                        "high",
                        f"属性「{attribute}」在重叠时间内出现两个取值",
                        f"{entity_name} 的 {attribute} 同时被记为 "
                        f"{_canonical(_loads(left['value_json'], left['value_json']))} 与 "
                        f"{_canonical(_loads(right['value_json'], right['value_json']))}。",
                        evidence={
                            "entity_id": entity_id,
                            "attribute": attribute,
                            "character_state_ids": [left["id"], right["id"]],
                        },
                        suggestion="给两次变化补上 event_id 与有效期，或撤回其中一条。",
                        key=f"{left['id']}|{right['id']}",
                    )
                )
    return findings, len(rows)


# --------------------------------------------------------------------------- #
# 5. 关系/称谓矛盾
# --------------------------------------------------------------------------- #
def _detect_relation(conn, project_id, branch_id, ctx) -> tuple[list[dict], int]:
    if not _table_exists(conn, "app_story_relation"):
        return [], 0
    rows = _rows(
        conn,
        "SELECT id,from_entity_id,to_entity_id,relation_type,note,status FROM app_story_relation "
        "WHERE project_id=? AND branch_id=?",
        (project_id, branch_id),
    )
    findings: list[dict] = []
    seen: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in rows:
        source = ctx["entities"].get(row["from_entity_id"])
        target = ctx["entities"].get(row["to_entity_id"])
        for role, entity_id, entity in (("起点", row["from_entity_id"], source), ("终点", row["to_entity_id"], target)):
            if entity is None:
                findings.append(
                    _finding(
                        "relation",
                        "high",
                        f"关系{role}指向不存在的实体",
                        f"关系 {row['id']}（{row['relation_type']}）的{role} {entity_id} 已不在实体表中。",
                        evidence={"relation_id": row["id"], "missing_entity_id": entity_id},
                        suggestion="修复或删除这条关系。",
                        key=f"{row['id']}:{role}",
                    )
                )
            elif entity["status"] != "active":
                findings.append(
                    _finding(
                        "relation",
                        "high",
                        f"关系{role}指向已归档实体",
                        f"关系 {row['id']}（{row['relation_type']}）的{role}「{entity['name']}」状态为 {entity['status']}。",
                        evidence={"relation_id": row["id"], "entity_id": entity_id, "status": entity["status"]},
                        suggestion="把关系标记为停用，或恢复该实体。",
                        key=f"{row['id']}:{role}",
                    )
                )
        if row["from_entity_id"] == row["to_entity_id"] and str(row["status"]) == "active":
            findings.append(
                _finding(
                    "relation",
                    "medium",
                    "实体与自己建立了关系",
                    f"关系 {row['id']} 的起点与终点都是「{(source or {}).get('name', row['from_entity_id'])}」。",
                    evidence={"relation_id": row["id"], "relation_type": row["relation_type"]},
                    suggestion="确认是否应为自指（如「心魔」），否则改到正确对象。",
                    key=row["id"],
                )
            )
        if str(row["status"]) == "active":
            seen.setdefault(
                (row["from_entity_id"], row["to_entity_id"], row["relation_type"]), []
            ).append(row)

    for (source_id, target_id, relation_type), items in sorted(seen.items()):
        if len(items) > 1:
            source = ctx["entities"].get(source_id, {}).get("name", source_id)
            target = ctx["entities"].get(target_id, {}).get("name", target_id)
            findings.append(
                _finding(
                    "relation",
                    "low",
                    "同一对人物存在重复关系",
                    f"{source} → {target} 的「{relation_type}」有 {len(items)} 条有效记录。",
                    evidence={"relation_ids": [item["id"] for item in items]},
                    suggestion="合并重复关系，避免上下文里出现两次。",
                    key=f"{source_id}|{target_id}|{relation_type}",
                )
            )
    return findings, len(rows)


# --------------------------------------------------------------------------- #
# 6. 伏笔断线
# --------------------------------------------------------------------------- #
def _detect_foreshadow(conn, project_id, branch_id, ctx) -> tuple[list[dict], int]:
    if not _table_exists(conn, "app_plot_item") or not _table_exists(conn, "app_foreshadow_link"):
        return [], 0
    has_archived = _has_column(conn, "app_plot_item", "archived")
    items = _rows(
        conn,
        "SELECT id,stage,text" + (",archived" if has_archived else ",0 archived")
        + " FROM app_plot_item WHERE project_id=? AND branch_id=?",
        (project_id, branch_id),
    )
    links = _rows(
        conn,
        "SELECT id,plot_id,chapter_id,kind,note FROM app_foreshadow_link "
        "WHERE project_id=? AND branch_id=?",
        (project_id, branch_id),
    )
    by_plot: dict[str, list[sqlite3.Row]] = {}
    for link in links:
        by_plot.setdefault(link["plot_id"], []).append(link)

    findings: list[dict] = []
    for item in items:
        if has_archived and item["archived"]:
            continue
        item_links = by_plot.get(item["id"], [])
        plants = [link for link in item_links if link["kind"] == "plant"]
        payoffs = [link for link in item_links if link["kind"] == "payoff"]
        text = str(item["text"] or "").strip()
        label = text[:40] + ("…" if len(text) > 40 else "") if text else item["id"]

        if payoffs and not plants:
            findings.append(
                _finding(
                    "foreshadow",
                    "high",
                    "伏笔只有回收、没有埋设",
                    f"「{label}」有 {len(payoffs)} 条 payoff，但没有任何 plant，读者没有见过这个伏笔。",
                    evidence={"plot_id": item["id"], "payoff_ids": [link["id"] for link in payoffs]},
                    suggestion="补一条 plant，或把 payoff 改到真正埋过的地方。",
                    key=f"{item['id']}:no-plant",
                )
            )
        if item["stage"] == "payoff" and not payoffs:
            findings.append(
                _finding(
                    "foreshadow",
                    "medium",
                    "标记为回收却没有回收记录",
                    f"「{label}」的 stage=payoff，但没有任何 payoff 链接指向具体章节。",
                    evidence={"plot_id": item["id"]},
                    suggestion="补上回收章节，或把 stage 改回 develop。",
                    key=f"{item['id']}:no-payoff",
                )
            )
        if item["stage"] != "payoff" and plants and not payoffs:
            ordinals = [
                ctx["chapters"].get(str(link["chapter_id"]), {}).get("ordinal")
                for link in plants
                if link["chapter_id"]
            ]
            ordinals = [value for value in ordinals if isinstance(value, int)]
            if ordinals and ctx["max_ordinal"] - max(ordinals) >= 15:
                findings.append(
                    _finding(
                        "foreshadow",
                        "medium",
                        "伏笔埋设超过 15 章仍未回收",
                        f"「{label}」在第 {max(ordinals)} 章埋下，当前已写到第 {ctx['max_ordinal']} 章，仍未见回收。",
                        evidence={"plot_id": item["id"], "plant_ids": [link["id"] for link in plants]},
                        suggestion="安排回收章节，或显式标记为 abandoned 并说明原因。",
                        key=f"{item['id']}:stale",
                    )
                )
        if not item_links and item["stage"] == "setup" and text:
            findings.append(
                _finding(
                    "foreshadow",
                    "low",
                    "伏笔没有任何章节链接",
                    f"「{label}」还停留在 setup，没有 plant 指向具体章节，无法被检索到。",
                    evidence={"plot_id": item["id"]},
                    suggestion="把埋设位置链接到章节，让长程召回能定位它。",
                    key=f"{item['id']}:unlinked",
                )
            )
    return findings, len(items)


# --------------------------------------------------------------------------- #
# 7. 来源失效（"每句话有来路"）
# --------------------------------------------------------------------------- #
def _detect_evidence(conn, project_id, branch_id, ctx) -> tuple[list[dict], int]:
    if not _table_exists(conn, "assertion"):
        return [], 0
    rows = _rows(
        conn,
        "SELECT id,subject_entity_id,evidence_json,reviewed_by,reviewed_at FROM assertion "
        "WHERE project_id=? AND branch_id=? AND state='canon'",
        (project_id, branch_id),
    )
    spans: dict[str, sqlite3.Row] = {}
    if _table_exists(conn, "source_span"):
        for row in _rows(
            conn,
            "SELECT id,revision_id,document_node_id,status FROM source_span WHERE project_id=?",
            (project_id,),
        ):
            spans[row["id"]] = row

    findings: list[dict] = []
    for row in rows:
        evidence = _loads(row["evidence_json"], [])
        if not isinstance(evidence, list) or not evidence:
            findings.append(
                _finding(
                    "evidence",
                    "blocker",
                    "已确认事实没有来源",
                    f"断言 {row['id']} 处于 canon，但 evidence 为空，无法回到原句。",
                    evidence={"assertion_id": row["id"]},
                    suggestion="补 source_span 证据，或降级为 candidate。",
                    key=f"{row['id']}:empty",
                )
            )
            continue
        if not row["reviewed_by"] or not row["reviewed_at"]:
            findings.append(
                _finding(
                    "evidence",
                    "blocker",
                    "已确认事实缺少复核人",
                    f"断言 {row['id']} 处于 canon，但没有 reviewed_by / reviewed_at。",
                    evidence={"assertion_id": row["id"]},
                    suggestion="补复核记录；无法补齐时降级为 candidate。",
                    key=f"{row['id']}:review",
                )
            )
        for entry in evidence:
            if not isinstance(entry, dict):
                continue
            span_id = entry.get("source_span_id")
            if not span_id:
                continue
            span = spans.get(str(span_id))
            if span is None:
                findings.append(
                    _finding(
                        "evidence",
                        "high",
                        "来源片段已不存在",
                        f"断言 {row['id']} 引用的 source_span {span_id} 已被删除。",
                        evidence={"assertion_id": row["id"], "source_span_id": span_id},
                        suggestion="重新定位原句并 reanchor，或降级这条事实。",
                        key=f"{row['id']}:{span_id}:missing",
                    )
                )
                continue
            if span["status"] != "valid":
                findings.append(
                    _finding(
                        "evidence",
                        "high",
                        "来源片段已失效",
                        f"断言 {row['id']} 的来源片段状态为 {span['status']}，正文改动后原句无法再核对。",
                        evidence={"assertion_id": row["id"], "source_span_id": span_id, "status": span["status"]},
                        suggestion="重新定位原句并 reanchor。",
                        key=f"{row['id']}:{span_id}:{span['status']}",
                    )
                )
                continue
            latest = ctx["latest_revision"].get(span["document_node_id"])
            if latest and span["revision_id"] != latest:
                findings.append(
                    _finding(
                        "evidence",
                        "medium",
                        "来源片段属于旧版本正文",
                        f"断言 {row['id']} 的来源片段指向旧 revision，当前章节已有更新版本。",
                        evidence={
                            "assertion_id": row["id"],
                            "source_span_id": span_id,
                            "span_revision": span["revision_id"],
                            "latest_revision": latest,
                        },
                        suggestion="确认旧原句是否仍然成立，必要时 reanchor。",
                        key=f"{row['id']}:{span_id}:old",
                    )
                )
    return findings, len(rows)


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
DETECTORS = (
    ("fact", _detect_fact),
    ("timeline", _detect_timeline),
    ("knowledge", _detect_knowledge),
    ("attribute", _detect_attribute),
    ("relation", _detect_relation),
    ("foreshadow", _detect_foreshadow),
    ("evidence", _detect_evidence),
)


def _grade(score: float) -> str:
    for threshold, label in _GRADE_THRESHOLDS:
        if score >= threshold:
            return label
    return "D"


def analyze(
    conn: sqlite3.Connection,
    project_id: str,
    branch_id: str,
    *,
    min_severity: str | None = None,
) -> dict[str, Any]:
    """对指定 project/branch 跑全部检测，返回可序列化的报告。"""
    if min_severity is not None and min_severity not in SEVERITY_ORDER:
        raise ValueError(
            "min_severity 只能是 " + " / ".join(SEVERITY_ORDER) + "，收到：" + str(min_severity)
        )
    if _table_exists(conn, "project"):
        exists = conn.execute("SELECT 1 FROM project WHERE id=?", (project_id,)).fetchone()
        if not exists:
            # 不存在的作用域不能返回 100 分 A——那会让「没检查」看起来像「很干净」。
            raise ConsistencyScopeError(f"项目不存在：{project_id}")
    if _table_exists(conn, "branch"):
        exists = conn.execute("SELECT 1 FROM branch WHERE id=?", (branch_id,)).fetchone()
        if not exists:
            raise ConsistencyScopeError(f"分支不存在：{branch_id}")

    ctx = _build_context(conn, project_id, branch_id)
    findings: list[dict] = []
    examined: dict[str, int] = {}
    for name, detector in DETECTORS:
        detected, count = detector(conn, project_id, branch_id, ctx)
        findings.extend(detected)
        examined[name] = count

    findings.sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], item["category"], item["id"]))

    # 分数、分类统计永远基于全量检查结果；min_severity 只裁剪返回清单，
    # 否则「只看高危」会把分数抬回 100，等于用筛选器美化体检报告。
    counts = {severity: 0 for severity in SEVERITY_WEIGHTS}
    for item in findings:
        counts[item["severity"]] += 1

    deduction = sum(SEVERITY_WEIGHTS[item["severity"]] for item in findings)
    score = round(max(0.0, 100.0 - deduction), 1)

    categories = {}
    for name in CATEGORY_LABELS:
        items = [item for item in findings if item["category"] == name]
        categories[name] = {
            "label": CATEGORY_LABELS[name],
            "count": len(items),
            "blocker": sum(1 for item in items if item["severity"] == "blocker"),
            "high": sum(1 for item in items if item["severity"] == "high"),
            "medium": sum(1 for item in items if item["severity"] == "medium"),
            "low": sum(1 for item in items if item["severity"] == "low"),
        }

    grade = _grade(score)
    # 分数分段不能盖过最严重的问题：92 分 + 一条 high 事实矛盾不该显示 A。
    if counts["blocker"]:
        grade = "C" if grade in {"A", "B"} else grade
    elif counts["high"]:
        grade = "B" if grade == "A" else grade

    visible = findings
    if min_severity:
        cutoff = SEVERITY_ORDER.get(min_severity, 99)
        visible = [item for item in findings if SEVERITY_ORDER[item["severity"]] <= cutoff]

    total_examined = sum(examined.values())
    summary = (
        f"一致性分数 {score}（{grade}）：核对 {total_examined} 项次"
        "（同一记录会被多类检测重复核对），"
        f"发现 {len(findings)} 个问题"
        f"（阻断 {counts['blocker']}、高 {counts['high']}、中 {counts['medium']}、低 {counts['low']}）。"
    )
    if not findings:
        summary = f"一致性分数 100（A）：核对 {total_examined} 项次，未发现矛盾。"

    return {
        "engine": "moyu-consistency",
        "version": ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "project_id": project_id,
        "branch_id": branch_id,
        "score": score,
        "grade": grade,
        "counts": counts,
        "categories": categories,
        "examined": examined,
        "total_examined": total_examined,
        "findings": visible,
        "all_findings": findings,
        "returned": len(visible),
        "filtered_by": min_severity or None,
        "summary": summary,
    }


# --------------------------------------------------------------------------- #
# 文本渲染与 CLI
# --------------------------------------------------------------------------- #
def render_text(report: Mapping[str, Any]) -> str:
    lines = [
        report["summary"],
        f"项目 {report['project_id']} / 分支 {report['branch_id']}",
        "",
        "分类统计：",
    ]
    for name in CATEGORY_LABELS:
        item = report["categories"][name]
        if item["count"]:
            lines.append(
                f"  - {item['label']}：{item['count']}（阻断 {item['blocker']}、高 {item['high']}、"
                f"中 {item['medium']}、低 {item['low']}）"
            )
    if not report["findings"]:
        return "\n".join(lines)
    lines.append("")
    lines.append("问题清单：")
    for index, finding in enumerate(report["findings"], 1):
        lines.append(
            f"  {index}. [{SEVERITY_LABELS[finding['severity']]}] "
            f"{finding['category_label']} · {finding['title']}"
        )
        lines.append(f"     {finding['detail']}")
        if finding.get("suggestion"):
            lines.append(f"     建议：{finding['suggestion']}")
    return "\n".join(lines)


def _resolve_scope(
    conn: sqlite3.Connection, project_id: str | None, branch_id: str | None
) -> tuple[str, str]:
    if not project_id:
        row = conn.execute("SELECT id FROM project ORDER BY rowid LIMIT 1").fetchone()
        if row is None:
            raise SystemExit("数据库里没有任何作品")
        project_id = row[0]
    if not branch_id:
        row = conn.execute(
            "SELECT id FROM branch WHERE project_id=? AND status='active' ORDER BY rowid LIMIT 1",
            (project_id,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"作品 {project_id} 没有 active 分支")
        branch_id = row[0]
    return project_id, branch_id


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="墨屿一致性引擎：检测故事状态中的矛盾")
    parser.add_argument("--db", default=os.environ.get("MOYU_DB", "moyu.sqlite3"))
    parser.add_argument("--project", default=None)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument(
        "--min-severity",
        choices=sorted(SEVERITY_WEIGHTS, key=lambda item: SEVERITY_ORDER[item]),
        default=None,
    )
    parser.add_argument(
        "--fail-on",
        choices=sorted(SEVERITY_WEIGHTS, key=lambda item: SEVERITY_ORDER[item]),
        default=None,
        help="存在该等级及以上的问题时以非零状态退出（用于 CI 门禁）",
    )
    args = parser.parse_args(argv)

    path = Path(args.db)
    if not path.exists():
        raise SystemExit(f"数据库不存在：{path}")
    conn = sqlite3.connect(str(path))
    try:
        try:
            project_id, branch_id = _resolve_scope(conn, args.project, args.branch)
            report = analyze(conn, project_id, branch_id, min_severity=args.min_severity)
        except ConsistencyScopeError as exc:
            # 作用域写错是用户输入问题，不该甩一段 traceback 给 CI。
            print(f"一致性检查失败：{exc}", file=sys.stderr)
            return 2
    finally:
        conn.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))

    if args.fail_on:
        cutoff = SEVERITY_ORDER[args.fail_on]
        # report["findings"] 已被 --min-severity 裁剪；门禁必须看全量，
        # 否则「--min-severity blocker --fail-on high」会假绿。
        offenders = [
            item for item in report.get("all_findings") or report["findings"]
            if SEVERITY_ORDER[item["severity"]] <= cutoff
        ]
        if offenders:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
