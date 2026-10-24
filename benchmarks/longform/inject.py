"""LongFormBench 已知矛盾注入器。

在 ``generate.py`` 产出的干净项目上注入**已知的**一致性矛盾，并留下 ground truth，
用于计算引擎的检出率（recall）、误报数与按类召回。

每条注入都有稳定 id、类别、期望严重度与**匹配锚点**（锚点是 finding.evidence 里的
稳定主键，例如 assertion id / plot_id / relation id）。引擎的 finding id 是内容哈希，
因此只能用 evidence 里的业务主键做匹配。

覆盖七类：fact / timeline / knowledge / attribute / relation / foreshadow / evidence。

用法::

    .venv/bin/python benchmarks/longform/inject.py --db /tmp/longform.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.longform.generate import (  # noqa: E402
    ACTOR_ID,
    BRANCH_ID,
    PROJECT_ID,
    REVIEWED_AT,
    REVIEWER,
)

CATEGORY_LABELS = {
    "fact": "事实矛盾",
    "timeline": "时间线矛盾",
    "knowledge": "知识越界",
    "attribute": "属性漂移",
    "relation": "关系/称谓矛盾",
    "foreshadow": "伏笔断线",
    "evidence": "来源失效",
}

# 每条注入的静态元数据。实际写入由 inject() 里的同名分支完成。
INJECTION_SPECS: list[dict[str, Any]] = [
    # ---- fact (6) ----
    {"id": "inj-fact-01", "category": "fact", "severity": "high",
     "description": "沈砚舟的身份同时被记为「天枢阁少主」与「玄衣卫密探」"},
    {"id": "inj-fact-02", "category": "fact", "severity": "high",
     "description": "苏晚棠的所属同时被记为「药王谷」与「听雪楼」"},
    {"id": "inj-fact-03", "category": "fact", "severity": "high",
     "description": "青霜剑的持有者同时被记为「沈砚舟」与「陆青崖」"},
    {"id": "inj-fact-04", "category": "fact", "severity": "high",
     "description": "天枢阁的首领同时被记为「韩九霄」与「沈砚舟」"},
    {"id": "inj-fact-05", "category": "fact", "severity": "high",
     "description": "雾隐山的地域同时被记为「北境」与「南境」"},
    {"id": "inj-fact-06", "category": "fact", "severity": "high",
     "description": "雪隼的 has_story_fact 被记成另一个名字「白隼」"},
    # ---- timeline (5) ----
    {"id": "inj-timeline-01", "category": "timeline", "severity": "high",
     "description": "时间线事件挂在已被删除的章节 ch099 上"},
    {"id": "inj-timeline-02", "category": "timeline", "severity": "high",
     "description": "断言 valid_start 晚于 valid_end，事实永远不会生效"},
    {"id": "inj-timeline-03", "category": "timeline", "severity": "medium",
     "description": "同一章节 ch010 出现两个不同故事时间"},
    {"id": "inj-timeline-04", "category": "timeline", "severity": "medium",
     "description": "同一序位 901 挂着两个不同章节的事件"},
    {"id": "inj-timeline-05", "category": "timeline", "severity": "high",
     "description": "第二个时间线事件挂在已删除章节 ch098 上"},
    # ---- knowledge (4) ----
    {"id": "inj-knowledge-01", "category": "knowledge", "severity": "high",
     "description": "陆青崖相信自己不知道的事（known_by 只有沈砚舟）"},
    {"id": "inj-knowledge-02", "category": "knowledge", "severity": "medium",
     "description": "只对苏晚棠可见的私密记忆被提升为 world_truth"},
    {"id": "inj-knowledge-03", "category": "knowledge", "severity": "low",
     "description": "非人物实体「青霜剑」被标记为持有信念"},
    {"id": "inj-knowledge-04", "category": "knowledge", "severity": "high",
     "description": "温如玉相信自己不知道的事（known_by 只有韩九霄）"},
    # ---- attribute (4) ----
    {"id": "inj-attribute-01", "category": "attribute", "severity": "high",
     "description": "沈砚舟的武力在重叠时间内同时为「二流」与「绝顶」"},
    {"id": "inj-attribute-02", "category": "attribute", "severity": "high",
     "description": "苏晚棠的年龄在重叠时间内同时为 22 与 25"},
    {"id": "inj-attribute-03", "category": "attribute", "severity": "high",
     "description": "陆青崖的伤势在重叠时间内同时为「右臂刀伤未愈」与「无」"},
    {"id": "inj-attribute-04", "category": "attribute", "severity": "high",
     "description": "阿箬的伤势在重叠时间内同时为「无」与「重伤」"},
    # ---- relation (5) ----
    {"id": "inj-relation-01", "category": "relation", "severity": "high",
     "description": "关系终点指向不存在的实体 ent_missing"},
    {"id": "inj-relation-02", "category": "relation", "severity": "high",
     "description": "关系终点指向已归档实体「断刃」"},
    {"id": "inj-relation-03", "category": "relation", "severity": "medium",
     "description": "裴照雪与自己建立 active 关系「心魔」"},
    {"id": "inj-relation-04", "category": "relation", "severity": "low",
     "description": "陆青崖→裴照雪的「同门」存在两条 active 记录"},
    {"id": "inj-relation-05", "category": "relation", "severity": "high",
     "description": "关系起点指向已归档实体「断刃」"},
    # ---- foreshadow (5) ----
    {"id": "inj-foreshadow-01", "category": "foreshadow", "severity": "high",
     "description": "伏笔只有 payoff、没有 plant"},
    {"id": "inj-foreshadow-02", "category": "foreshadow", "severity": "medium",
     "description": "伏笔 stage=payoff 但没有任何 payoff 链接"},
    {"id": "inj-foreshadow-03", "category": "foreshadow", "severity": "medium",
     "description": "伏笔在第 5 章埋下，写到第 40 章仍未回收"},
    {"id": "inj-foreshadow-04", "category": "foreshadow", "severity": "low",
     "description": "setup 伏笔没有任何章节链接"},
    {"id": "inj-foreshadow-05", "category": "foreshadow", "severity": "high",
     "description": "第二条只有 payoff、没有 plant 的伏笔"},
    # ---- evidence (6) ----
    {"id": "inj-evidence-01", "category": "evidence", "severity": "blocker",
     "description": "canon 断言 evidence 为空"},
    {"id": "inj-evidence-02", "category": "evidence", "severity": "blocker",
     "description": "第二条 canon 断言 evidence 为空"},
    {"id": "inj-evidence-03", "category": "evidence", "severity": "blocker",
     "description": "canon 断言缺少 reviewed_by / reviewed_at"},
    {"id": "inj-evidence-04", "category": "evidence", "severity": "high",
     "description": "断言引用的 source_span 不存在"},
    {"id": "inj-evidence-05", "category": "evidence", "severity": "high",
     "description": "断言引用的 source_span 状态为 stale"},
    {"id": "inj-evidence-06", "category": "evidence", "severity": "medium",
     "description": "断言引用的 source_span 属于旧 revision"},
]

SPEC_BY_ID = {spec["id"]: spec for spec in INJECTION_SPECS}

# 注入集依赖至少 15 章的跨度（inj-foreshadow-03），且各注入需要彼此独立的章节锚点。
# 低于该章节数时锚点会挤在同一章，产生与引擎无关的假误报，因此直接拒绝。
MIN_CHAPTERS = 25


def _now() -> str:
    return REVIEWED_AT


def _spec(injection_id: str) -> dict[str, Any]:
    return dict(SPEC_BY_ID[injection_id])


def _insert_canon_assertion(
    conn: sqlite3.Connection,
    *,
    assertion_id: str,
    entity_id: str,
    predicate: str,
    obj: Any,
    evidence: list[dict[str, Any]] | None = None,
    epistemic: str = "world_truth",
    valid_start: str | None = None,
    valid_end: str | None = None,
    reviewed_by: str | None = REVIEWER,
    reviewed_at: str | None = REVIEWED_AT,
    state: str = "canon",
) -> None:
    conn.execute(
        "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
        "epistemic_mode,state,valid_start,valid_end,confidence,supersedes_id,evidence_json,"
        "reviewed_by,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)",
        (
            assertion_id,
            PROJECT_ID,
            BRANCH_ID,
            entity_id,
            predicate,
            json.dumps(obj, ensure_ascii=False, sort_keys=True),
            epistemic,
            state,
            valid_start,
            valid_end,
            0.9,
            json.dumps(evidence if evidence is not None else [], ensure_ascii=False),
            reviewed_by,
            reviewed_at,
        ),
    )


def _drop_canon_triggers(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """临时移除 canon 写入约束，返回 (name, sql) 以便恢复。"""
    saved: list[tuple[str, str]] = []
    for name in ("check_assertion_canon_insert", "check_assertion_canon_update"):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
        ).fetchone()
        if row and row[0]:
            saved.append((name, row[0]))
            conn.execute(f"DROP TRIGGER {name}")
    conn.commit()
    return saved


def _restore_canon_triggers(conn: sqlite3.Connection, saved: Iterable[tuple[str, str]]) -> None:
    for name, sql in saved:
        conn.execute(sql)
    conn.commit()


def _first_span(conn: sqlite3.Connection, chapter_ordinal: int) -> str:
    row = conn.execute(
        "SELECT id FROM source_span WHERE project_id=? AND document_node_id=? ORDER BY id LIMIT 1",
        (PROJECT_ID, f"ch{chapter_ordinal:03d}"),
    ).fetchone()
    if not row:
        raise RuntimeError(f"章节 ch{chapter_ordinal:03d} 没有可用的来源片段")
    return row[0]


def _entity(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT id FROM entity WHERE project_id=? AND canonical_name=?", (PROJECT_ID, name)
    ).fetchone()
    if not row:
        raise RuntimeError(f"实体不存在：{name}")
    return row[0]


def _chapter_ordinals(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT MAX(ordinal) FROM document_node WHERE project_id=? AND branch_id=? AND kind='chapter'",
        (PROJECT_ID, BRANCH_ID),
    ).fetchone()
    return int(row[0] or 0)


# --------------------------------------------------------------------------- #
# 注入实现
# --------------------------------------------------------------------------- #
def inject(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """在干净项目上注入全部已知矛盾，返回 ground truth 列表。"""
    ground_truth: list[dict[str, Any]] = []

    def record(injection_id: str, match: Mapping[str, list[str]]) -> None:
        spec = _spec(injection_id)
        spec["match"] = {key: list(values) for key, values in match.items()}
        spec["expected_findings"] = 1
        ground_truth.append(spec)

    total_chapters = _chapter_ordinals(conn)
    if total_chapters < MIN_CHAPTERS:
        raise ValueError(
            f"LongFormBench 注入集需要至少 {MIN_CHAPTERS} 章（当前 {total_chapters} 章）："
            "伏笔断线规则依赖 15 章跨度，且各注入需要彼此独立的章节锚点。"
            "请用 --chapters 40（约 10.5 万汉字）或 --chapters 25 以上。"
        )

    def span_at(ordinal: int) -> str:
        """取第 ordinal 章的第一个来源片段；越界时夹到最后一章。"""
        return _first_span(conn, min(max(1, ordinal), total_chapters))

    def chapter_at(ordinal: int) -> str:
        """第 ordinal 章的 document_node id；越界时夹到 [1, total_chapters]。"""
        return f"ch{min(max(1, ordinal), total_chapters):03d}"

    # ------------------------------------------------------------------ fact
    # 1) 沈砚舟 身份
    _insert_canon_assertion(
        conn, assertion_id="as_inj_fact01", entity_id=_entity(conn, "沈砚舟"),
        predicate="身份", obj={"value": "玄衣卫密探"},
        evidence=[{"source_span_id": span_at(12)}],
    )
    record("inj-fact-01", {"assertion_ids": ["as_inj_fact01"]})

    # 2) 苏晚棠 所属
    _insert_canon_assertion(
        conn, assertion_id="as_inj_fact02", entity_id=_entity(conn, "苏晚棠"),
        predicate="所属", obj={"value": "听雪楼"},
        evidence=[{"source_span_id": span_at(13)}],
    )
    record("inj-fact-02", {"assertion_ids": ["as_inj_fact02"]})

    # 3) 青霜剑 持有者
    _insert_canon_assertion(
        conn, assertion_id="as_inj_fact03", entity_id=_entity(conn, "青霜剑"),
        predicate="持有者", obj={"value": "陆青崖"},
        evidence=[{"source_span_id": span_at(14)}],
    )
    record("inj-fact-03", {"assertion_ids": ["as_inj_fact03"]})

    # 4) 天枢阁 首领
    _insert_canon_assertion(
        conn, assertion_id="as_inj_fact04", entity_id=_entity(conn, "天枢阁"),
        predicate="首领", obj={"value": "沈砚舟"},
        evidence=[{"source_span_id": span_at(15)}],
    )
    record("inj-fact-04", {"assertion_ids": ["as_inj_fact04"]})

    # 5) 雾隐山 地域
    _insert_canon_assertion(
        conn, assertion_id="as_inj_fact05", entity_id=_entity(conn, "雾隐山"),
        predicate="地域", obj={"value": "南境"},
        evidence=[{"source_span_id": span_at(16)}],
    )
    record("inj-fact-05", {"assertion_ids": ["as_inj_fact05"]})

    # 6) 雪隼 has_story_fact 被记成另一个名字
    _insert_canon_assertion(
        conn, assertion_id="as_inj_fact06", entity_id=_entity(conn, "雪隼"),
        predicate="has_story_fact",
        obj={"name": "白隼", "type": "妖兽", "note": "误记的同名妖兽"},
        evidence=[{"source_span_id": span_at(17)}],
    )
    record("inj-fact-06", {"assertion_ids": ["as_inj_fact06"]})

    # -------------------------------------------------------------- timeline
    # 1) 事件挂在已删除章节 ch099
    conn.execute(
        "INSERT INTO document_node(id,project_id,branch_id,parent_id,kind,title,ordinal) "
        "VALUES('ch099',?,?,NULL,'chapter','已删除的第九十九章',99)",
        (PROJECT_ID, BRANCH_ID),
    )
    conn.execute(
        "INSERT INTO app_deleted_node(chapter_id,deleted_at,reason) VALUES('ch099',?,'作者删除')",
        (_now(),),
    )
    conn.execute(
        "INSERT INTO app_timeline_event(id,project_id,branch_id,title,event_time,chapter_id,"
        "entity_ids_json,summary,status,ordinal,created_at,updated_at,evidence_json) "
        "VALUES('evt_inj_tl01',?,?,'幽灵事件','承平九年春','ch099','[]','','planned',950,?,?,'{}')",
        (PROJECT_ID, BRANCH_ID, _now(), _now()),
    )
    record("inj-timeline-01", {"event_id": ["evt_inj_tl01"]})

    # 2) valid_start 晚于 valid_end
    _insert_canon_assertion(
        conn, assertion_id="as_inj_tl02", entity_id=_entity(conn, "白露"),
        predicate="行踪", obj={"value": "青石镇"},
        evidence=[{"source_span_id": span_at(18)}],
        valid_start="2026-05-01", valid_end="2026-01-01",
    )
    record("inj-timeline-02", {"assertion_id": ["as_inj_tl02"]})

    # 3) 同一章节 ch010 两个故事时间
    conn.execute(
        "INSERT INTO app_timeline_event(id,project_id,branch_id,title,event_time,chapter_id,"
        "entity_ids_json,summary,status,ordinal,created_at,updated_at,evidence_json) "
        "VALUES('evt_inj_tl03',?,?,'章内插叙','承平六年春',?,'[]','','planned',951,?,?,'{}')",
        (PROJECT_ID, BRANCH_ID, chapter_at(10), _now(), _now()),
    )
    record("inj-timeline-03", {"chapter_id": [chapter_at(10)]})

    # 4) 同一序位 901 挂着两个不同章节的事件。
    #    事件时间沿用各自章节已有时间，确保只触发「同一序位多章节」，不连带触发
    #    「同一章节多个故事时间」。
    def _chapter_time(chapter_id: str) -> str:
        row = conn.execute(
            "SELECT event_time FROM app_timeline_event WHERE project_id=? AND branch_id=? "
            "AND chapter_id=? AND event_time<>'' ORDER BY ordinal LIMIT 1",
            (PROJECT_ID, BRANCH_ID, chapter_id),
        ).fetchone()
        if not row:
            raise RuntimeError(f"章节 {chapter_id} 没有可复用的故事时间")
        return str(row[0])

    for suffix, chapter in (("a", chapter_at(11)), ("b", chapter_at(12))):
        conn.execute(
            "INSERT INTO app_timeline_event(id,project_id,branch_id,title,event_time,chapter_id,"
            "entity_ids_json,summary,status,ordinal,created_at,updated_at,evidence_json) "
            "VALUES(?,?,?,?,?,?,'[]','','planned',901,?,?,'{}')",
            (f"evt_inj_tl04{suffix}", PROJECT_ID, BRANCH_ID, f"并列事件{suffix}",
             _chapter_time(chapter), chapter, _now(), _now()),
        )
    record("inj-timeline-04", {"event_ids": ["evt_inj_tl04a", "evt_inj_tl04b"]})

    # 5) 第二个事件挂在已删除章节 ch098
    conn.execute(
        "INSERT INTO document_node(id,project_id,branch_id,parent_id,kind,title,ordinal) "
        "VALUES('ch098',?,?,NULL,'chapter','已删除的第九十八章',98)",
        (PROJECT_ID, BRANCH_ID),
    )
    conn.execute(
        "INSERT INTO app_deleted_node(chapter_id,deleted_at,reason) VALUES('ch098',?,'作者删除')",
        (_now(),),
    )
    conn.execute(
        "INSERT INTO app_timeline_event(id,project_id,branch_id,title,event_time,chapter_id,"
        "entity_ids_json,summary,status,ordinal,created_at,updated_at,evidence_json) "
        "VALUES('evt_inj_tl05',?,?,'第二个幽灵事件','承平九年夏','ch098','[]','','planned',952,?,?,'{}')",
        (PROJECT_ID, BRANCH_ID, _now(), _now()),
    )
    record("inj-timeline-05", {"event_id": ["evt_inj_tl05"]})

    # ------------------------------------------------------------- knowledge
    def _memory(memory_id: str, entity_id: str, payload: dict[str, Any], span_id: str) -> None:
        conn.execute(
            "INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,entity_id,kind,"
            "payload_json,status,confidence,created_at,reviewed_at,reviewed_by) "
            "VALUES(?,?,?,?,?,'fact',?,'accepted',0.9,?,?,?)",
            (memory_id, PROJECT_ID, BRANCH_ID, span_id, entity_id,
             json.dumps(payload, ensure_ascii=False), _now(), _now(), REVIEWER),
        )

    # 1) 陆青崖 相信自己不知道的事
    _memory("cm_inj01", _entity(conn, "陆青崖"),
            {"known_by": ["沈砚舟"], "claim": "沈家旧案的真正主使"}, span_at(19))
    _insert_canon_assertion(
        conn, assertion_id="as_inj_kn01", entity_id=_entity(conn, "陆青崖"),
        predicate="知晓", obj={"value": "沈家旧案的真正主使"},
        evidence=[{"source_span_id": span_at(19), "candidate_memory_id": "cm_inj01"}],
        epistemic="character_belief",
    )
    record("inj-knowledge-01", {"assertion_id": ["as_inj_kn01"]})

    # 2) 私密记忆被提升为世界事实
    _memory("cm_inj02", _entity(conn, "苏晚棠"),
            {"known_by": ["苏晚棠"], "private": True, "claim": "药王谷谷主中毒"}, span_at(20))
    _insert_canon_assertion(
        conn, assertion_id="as_inj_kn02", entity_id=_entity(conn, "苏晚棠"),
        predicate="密闻", obj={"value": "药王谷谷主中毒"},
        evidence=[{"source_span_id": span_at(20), "candidate_memory_id": "cm_inj02"}],
        epistemic="world_truth",
    )
    record("inj-knowledge-02", {"assertion_id": ["as_inj_kn02"]})

    # 3) 非人物实体持有信念（实体本身在 known_by 内，避免同时触发 high）
    _memory("cm_inj03", _entity(conn, "青霜剑"),
            {"known_by": ["沈砚舟", "青霜剑"], "claim": "剑中封着一缕残魂"}, span_at(21))
    _insert_canon_assertion(
        conn, assertion_id="as_inj_kn03", entity_id=_entity(conn, "青霜剑"),
        predicate="传闻", obj={"value": "剑中封着一缕残魂"},
        evidence=[{"source_span_id": span_at(21), "candidate_memory_id": "cm_inj03"}],
        epistemic="character_belief",
    )
    record("inj-knowledge-03", {"assertion_id": ["as_inj_kn03"]})

    # 4) 温如玉 相信自己不知道的事
    _memory("cm_inj04", _entity(conn, "温如玉"),
            {"known_by": ["韩九霄"], "claim": "天枢阁与玄衣卫的密约"}, span_at(22))
    _insert_canon_assertion(
        conn, assertion_id="as_inj_kn04", entity_id=_entity(conn, "温如玉"),
        predicate="知晓", obj={"value": "天枢阁与玄衣卫的密约"},
        evidence=[{"source_span_id": span_at(22), "candidate_memory_id": "cm_inj04"}],
        epistemic="character_belief",
    )
    record("inj-knowledge-04", {"assertion_id": ["as_inj_kn04"]})

    # ------------------------------------------------------------- attribute
    def _state(state_id: str, entity_name: str, attribute: str, value: Any,
               source_assertion_id: str) -> None:
        # 状态变更通常由一条尚未晋升为 canon 的候选断言驱动：先建 candidate 断言，
        # 再让 character_state 引用它。candidate 不参与 fact/evidence/knowledge 检测。
        entity_id = _entity(conn, entity_name)
        _insert_canon_assertion(
            conn, assertion_id=source_assertion_id, entity_id=entity_id,
            predicate=f"{attribute}变更", obj={"value": value},
            evidence=[{"source_span_id": span_at(25)}], state="candidate",
        )
        conn.execute(
            "INSERT INTO character_state(id,project_id,branch_id,entity_id,attribute,value_json,"
            "event_id,valid_start,valid_end,knowledge_scope_json,source_assertion_id) "
            "VALUES(?,?,?,?,?,?,NULL,'2026-01-01',NULL,'{}',?)",
            (state_id, PROJECT_ID, BRANCH_ID, entity_id, attribute,
             json.dumps({"value": value}, ensure_ascii=False), source_assertion_id),
        )

    _state("cst_inj01", "沈砚舟", "武力", "绝顶", "as_inj_attr01")
    record("inj-attribute-01", {"character_state_ids": ["cst_inj01"]})

    _state("cst_inj02", "苏晚棠", "年龄", 25, "as_inj_attr02")
    record("inj-attribute-02", {"character_state_ids": ["cst_inj02"]})

    _state("cst_inj03", "陆青崖", "伤势", "无", "as_inj_attr03")
    record("inj-attribute-03", {"character_state_ids": ["cst_inj03"]})

    _state("cst_inj04", "阿箬", "伤势", "重伤", "as_inj_attr04")
    record("inj-attribute-04", {"character_state_ids": ["cst_inj04"]})

    # -------------------------------------------------------------- relation
    def _relation(relation_id: str, source: str, target: str, relation_type: str,
                  status: str = "active", note: str = "") -> None:
        conn.execute(
            "INSERT INTO app_story_relation(id,project_id,branch_id,from_entity_id,to_entity_id,"
            "relation_type,note,status,created_at,updated_at,evidence_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'{}')",
            (relation_id, PROJECT_ID, BRANCH_ID, source, target, relation_type, note,
             status, _now(), _now()),
        )

    # 1) 终点指向不存在的实体：需要临时关闭外键
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        _relation("rel_inj01", _entity(conn, "沈砚舟"), "ent_missing", "旧识")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    record("inj-relation-01", {"relation_id": ["rel_inj01"]})

    # 2) 终点指向已归档实体「断刃」
    _relation("rel_inj02", _entity(conn, "沈砚舟"), _entity(conn, "断刃"), "旧主")
    record("inj-relation-02", {"relation_id": ["rel_inj02"]})

    # 3) 自指关系
    _relation("rel_inj03", _entity(conn, "裴照雪"), _entity(conn, "裴照雪"), "心魔")
    record("inj-relation-03", {"relation_id": ["rel_inj03"]})

    # 4) 重复 active 关系（clean 已有一条 陆青崖→裴照雪 同门）
    _relation("rel_inj04", _entity(conn, "陆青崖"), _entity(conn, "裴照雪"), "同门")
    record("inj-relation-04", {"relation_ids": ["rel_inj04"]})

    # 5) 起点指向已归档实体
    _relation("rel_inj05", _entity(conn, "断刃"), _entity(conn, "沈砚舟"), "旧部")
    record("inj-relation-05", {"relation_id": ["rel_inj05"]})

    # ------------------------------------------------------------ foreshadow
    def _plot(plot_id: str, stage: str, text: str) -> None:
        conn.execute(
            "INSERT INTO app_plot_item(id,project_id,branch_id,stage,text,created_at,updated_at,archived) "
            "VALUES(?,?,?,?,?,?,?,0)",
            (plot_id, PROJECT_ID, BRANCH_ID, stage, text, _now(), _now()),
        )

    def _link(link_id: str, plot_id: str, chapter_id: str | None, kind: str) -> None:
        conn.execute(
            "INSERT INTO app_foreshadow_link(id,project_id,branch_id,plot_id,chapter_id,kind,note,"
            "created_at,updated_at,evidence_json) VALUES(?,?,?,?,?,?,'',?,?,'{}')",
            (link_id, PROJECT_ID, BRANCH_ID, plot_id, chapter_id, kind, _now(), _now()),
        )

    # 1) 只有 payoff、没有 plant
    _plot("plot_inj01", "develop", "只有回收没有埋设的伏笔")
    _link("fsl_inj01a", "plot_inj01", chapter_at(30), "payoff")
    record("inj-foreshadow-01", {"plot_id": ["plot_inj01"]})

    # 2) stage=payoff 但没有 payoff 链接
    _plot("plot_inj02", "payoff", "声称已回收但无回收记录")
    record("inj-foreshadow-02", {"plot_id": ["plot_inj02"]})

    # 3) 第 5 章埋下，写到第 40 章仍未回收
    _plot("plot_inj03", "develop", "埋得太久没有回收的伏笔")
    _link("fsl_inj03a", "plot_inj03", chapter_at(5), "plant")
    record("inj-foreshadow-03", {"plot_id": ["plot_inj03"]})

    # 4) setup 且无任何链接
    _plot("plot_inj04", "setup", "完全没有落地的伏笔")
    record("inj-foreshadow-04", {"plot_id": ["plot_inj04"]})

    # 5) 第二条只有 payoff、没有 plant
    _plot("plot_inj05", "develop", "第二条只有回收的伏笔")
    _link("fsl_inj05a", "plot_inj05", chapter_at(35), "payoff")
    record("inj-foreshadow-05", {"plot_id": ["plot_inj05"]})

    # -------------------------------------------------------------- evidence
    saved_triggers = _drop_canon_triggers(conn)
    try:
        # 1) 空证据
        _insert_canon_assertion(
            conn, assertion_id="as_inj_ev01", entity_id=_entity(conn, "顾长风"),
            predicate="备注", obj={"value": "空证据注入"}, evidence=[],
        )
        record("inj-evidence-01", {"assertion_id": ["as_inj_ev01"]})

        # 2) 第二条空证据
        _insert_canon_assertion(
            conn, assertion_id="as_inj_ev02", entity_id=_entity(conn, "韩九霄"),
            predicate="备注", obj={"value": "第二条空证据注入"}, evidence=[],
        )
        record("inj-evidence-02", {"assertion_id": ["as_inj_ev02"]})

        # 3) 缺少复核人
        _insert_canon_assertion(
            conn, assertion_id="as_inj_ev03", entity_id=_entity(conn, "谢无咎"),
            predicate="备注", obj={"value": "缺复核人注入"},
            evidence=[{"source_span_id": span_at(23)}],
            reviewed_by=None, reviewed_at=None,
        )
        record("inj-evidence-03", {"assertion_id": ["as_inj_ev03"]})
    finally:
        _restore_canon_triggers(conn, saved_triggers)

    # 4) 引用不存在的 source_span
    _insert_canon_assertion(
        conn, assertion_id="as_inj_ev04", entity_id=_entity(conn, "秦慕白"),
        predicate="备注", obj={"value": "缺失来源注入"},
        evidence=[{"source_span_id": "span_missing_01"}],
    )
    record("inj-evidence-04", {"assertion_id": ["as_inj_ev04"]})

    # 5) 引用 stale 的 source_span
    stale_span_id = "span_inj_stale"
    conn.execute(
        "INSERT INTO source_span(id,project_id,revision_id,document_node_id,start_codepoint,"
        "end_codepoint,start_utf8,end_utf8,quote_hash,quote,anchor_before,anchor_after,status) "
        "SELECT ?,project_id,revision_id,document_node_id,start_codepoint,end_codepoint,start_utf8,"
        "end_utf8,quote_hash,quote,anchor_before,anchor_after,'stale' FROM source_span WHERE id=?",
        (stale_span_id, span_at(24)),
    )
    _insert_canon_assertion(
        conn, assertion_id="as_inj_ev05", entity_id=_entity(conn, "柳含烟"),
        predicate="备注", obj={"value": "失效来源注入"},
        evidence=[{"source_span_id": stale_span_id}],
    )
    record("inj-evidence-05", {"assertion_id": ["as_inj_ev05"]})

    # 6) 引用旧 revision 的 source_span（只对最后一章动手，避免污染其它断言的来源）
    last_chapter = f"ch{total_chapters:03d}"
    new_revision = f"rev{total_chapters:03d}b"
    conn.execute(
        "INSERT INTO document_revision(id,document_node_id,parent_id,branch_id,content_hash,"
        "canonical_text,created_at,actor_id,operation) "
        "SELECT ?,document_node_id,id,branch_id,content_hash,canonical_text,?,?,'update' "
        "FROM document_revision WHERE id=?",
        (new_revision, _now(), ACTOR_ID, f"rev{total_chapters:03d}"),
    )
    old_span_id = span_at(total_chapters)
    _insert_canon_assertion(
        conn, assertion_id="as_inj_ev06", entity_id=_entity(conn, "阿箬"),
        predicate="备注", obj={"value": "旧版本来源注入"},
        evidence=[{"source_span_id": old_span_id}],
    )
    record("inj-evidence-06", {"assertion_id": ["as_inj_ev06"]})

    conn.commit()
    return ground_truth


# --------------------------------------------------------------------------- #
# 匹配与统计
# --------------------------------------------------------------------------- #
def _evidence_ids(evidence: Mapping[str, Any]) -> dict[str, set[str]]:
    """把 finding.evidence 展平成 {字段: {字符串值}}。"""
    flattened: dict[str, set[str]] = {}
    for key, value in evidence.items():
        if isinstance(value, list):
            flattened[key] = {str(item) for item in value}
        elif isinstance(value, dict):
            continue
        else:
            flattened[key] = {str(value)}
    return flattened


def match_findings(
    findings: Iterable[Mapping[str, Any]], ground_truth: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """把引擎 finding 与 ground truth 对齐，计算 recall / 误报 / 按类召回。"""
    gt_list = [dict(item, matched_findings=[]) for item in ground_truth]
    unmatched: list[dict[str, Any]] = []

    for finding in findings:
        evidence = _evidence_ids(finding.get("evidence") or {})
        best: dict[str, Any] | None = None
        best_score = 0
        for candidate in gt_list:
            if candidate["category"] != finding["category"]:
                continue
            score = 0
            for key, values in candidate.get("match", {}).items():
                if key in evidence and evidence[key] & set(values):
                    score += 1
            if score > best_score:
                best, best_score = candidate, score
        if best is not None:
            best["matched_findings"].append(finding["id"])
        else:
            unmatched.append(finding)

    categories = sorted({item["category"] for item in gt_list})
    per_category: dict[str, dict[str, Any]] = {}
    for category in categories:
        expected = [item for item in gt_list if item["category"] == category]
        detected = [item for item in expected if item["matched_findings"]]
        false_positives = [item for item in unmatched if item["category"] == category]
        per_category[category] = {
            "label": CATEGORY_LABELS.get(category, category),
            "injected": len(expected),
            "detected": len(detected),
            "missed": len(expected) - len(detected),
            "false_positives": len(false_positives),
            "recall": round(len(detected) / len(expected), 4) if expected else None,
            "injected_ids": [item["id"] for item in expected],
            "detected_ids": [item["id"] for item in detected],
            "missed_ids": [item["id"] for item in expected if not item["matched_findings"]],
            "false_positive_finding_ids": [item["id"] for item in false_positives],
        }

    total_injected = len(gt_list)
    total_detected = sum(1 for item in gt_list if item["matched_findings"])
    return {
        "total_injected": total_injected,
        "total_detected": total_detected,
        "recall": round(total_detected / total_injected, 4) if total_injected else None,
        "total_findings": len(unmatched) + sum(len(item["matched_findings"]) for item in gt_list),
        "matched_findings": sum(len(item["matched_findings"]) for item in gt_list),
        "false_positives": len(unmatched),
        "false_positive_findings": [
            {
                "id": item["id"],
                "category": item["category"],
                "severity": item["severity"],
                "title": item["title"],
                "detail": item["detail"],
            }
            for item in unmatched
        ],
        "per_category": per_category,
        "missed": [
            {
                "id": item["id"],
                "category": item["category"],
                "severity": item["severity"],
                "description": item["description"],
            }
            for item in gt_list
            if not item["matched_findings"]
        ],
        "ground_truth": [
            {
                "id": item["id"],
                "category": item["category"],
                "category_label": CATEGORY_LABELS.get(item["category"], item["category"]),
                "severity": item["severity"],
                "description": item["description"],
                "matched_findings": item["matched_findings"],
                "detected": bool(item["matched_findings"]),
            }
            for item in gt_list
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="向 LongFormBench 项目注入已知矛盾")
    parser.add_argument("--db", required=True, help="已由 generate.py 生成的 SQLite 路径")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        ground_truth = inject(conn)
    finally:
        conn.close()
    print(json.dumps(ground_truth, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
