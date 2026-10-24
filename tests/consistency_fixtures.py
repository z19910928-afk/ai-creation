"""Executable regression fixture for the consistency engine.

Run: python tests/consistency_fixtures.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.moyu_consistency import SEVERITY_ORDER, analyze, render_text  # noqa: E402

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_deleted_node(
  chapter_id TEXT PRIMARY KEY REFERENCES document_node(id),
  deleted_at TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_story_relation(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id),
  from_entity_id TEXT NOT NULL REFERENCES entity(id), to_entity_id TEXT NOT NULL REFERENCES entity(id),
  relation_type TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS app_timeline_event(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id),
  title TEXT NOT NULL, event_time TEXT NOT NULL DEFAULT '', chapter_id TEXT REFERENCES document_node(id),
  entity_ids_json TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planned',
  ordinal INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS app_plot_item(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id),
  branch_id TEXT NOT NULL REFERENCES branch(id), stage TEXT NOT NULL
    CHECK(stage IN ('setup','develop','payoff')), text TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS app_foreshadow_link(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), branch_id TEXT NOT NULL REFERENCES branch(id),
  plot_id TEXT NOT NULL REFERENCES app_plot_item(id), chapter_id TEXT REFERENCES document_node(id),
  kind TEXT NOT NULL CHECK(kind IN ('plant','develop','payoff')), note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}'
);
"""

T = "2026-01-01T00:00:00+00:00"


def setup() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript((ROOT / "docs/schema-v1.sql").read_text(encoding="utf-8"))
    conn.executescript((ROOT / "docs/schema-v1.1-migration.sql").read_text(encoding="utf-8"))
    conn.executescript(APP_SCHEMA)
    return conn


def add_project(conn, project: str, branch: str) -> None:
    conn.execute("INSERT INTO project VALUES(?,?,?,?)", (project, 11, project.upper(), T))
    conn.execute(
        "INSERT INTO branch VALUES(?,?,NULL,NULL,NULL,'active','{}')", (branch, project)
    )
    conn.execute("INSERT INTO actor VALUES(?,?,?,?)", (f"actor_{project}", project, "user", "本地作者"))


def add_chapters(conn, project: str, branch: str, count: int, prefix: str = "n") -> None:
    for ordinal in range(1, count + 1):
        conn.execute(
            "INSERT INTO document_node VALUES(?,?,?,NULL,'chapter',?,?)",
            (f"{prefix}{ordinal}", project, branch, f"第{ordinal}章", ordinal),
        )


def add_revision(conn, node: str, revision: str, branch: str, text: str) -> None:
    conn.execute(
        "INSERT INTO document_revision VALUES(?,?,NULL,?,?,?,?,?,'create')",
        (revision, node, branch, "hash_" + revision, text.encode("utf-8"), T, "actor"),
    )


def add_span(conn, span: str, project: str, revision: str, node: str, quote: str, status: str = "valid") -> None:
    conn.execute(
        "INSERT INTO source_span(id,project_id,revision_id,document_node_id,start_codepoint,"
        "end_codepoint,start_utf8,end_utf8,quote_hash,quote,anchor_before,anchor_after,status) "
        "VALUES(?,?,?,?,0,?,0,?,'qh',?,NULL,NULL,?)",
        (span, project, revision, node, len(quote), len(quote.encode("utf-8")), quote, status),
    )


def add_assertion(
    conn,
    identifier: str,
    project: str,
    branch: str,
    entity: str,
    predicate: str,
    payload,
    *,
    state: str = "canon",
    epistemic: str = "world_truth",
    evidence=None,
    valid_start=None,
    valid_end=None,
    supersedes=None,
) -> None:
    conn.execute(
        "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
        "epistemic_mode,state,valid_start,valid_end,confidence,supersedes_id,evidence_json,"
        "reviewed_by,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identifier, project, branch, entity, predicate,
            json.dumps(payload, ensure_ascii=False), epistemic, state,
            valid_start, valid_end, 1.0, supersedes,
            json.dumps(evidence if evidence is not None else [{"source_span_id": "s1"}], ensure_ascii=False),
            "本地作者" if state == "canon" else None,
            T if state == "canon" else None,
        ),
    )


def seed_dirty(conn) -> None:
    add_project(conn, "p1", "b1")
    add_chapters(conn, "p1", "b1", 20)
    conn.execute(
        "INSERT INTO document_node VALUES('n99','p1','b1',NULL,'chapter','被删章节',99)"
    )
    conn.execute(
        "INSERT INTO app_deleted_node VALUES('n99',?,'作者删除')", (T,)
    )
    add_revision(conn, "n1", "r1", "b1", "沈砚站在青云城头。")
    add_revision(conn, "n2", "r2", "b1", "陈默收起旧剑。")
    add_revision(conn, "n3", "r3_old", "b1", "沈砚二十岁。")
    add_revision(conn, "n3", "r3_new", "b1", "沈砚二十一岁。")
    add_span(conn, "s1", "p1", "r1", "n1", "沈砚站在青云城头。")
    add_span(conn, "s2", "p1", "r1", "n1", "沈砚站在青云城头。", status="stale")
    add_span(conn, "s3", "p1", "r3_old", "n3", "沈砚二十岁。")

    conn.execute("INSERT INTO entity VALUES('e1','p1','character','沈砚','[]',NULL,'active')")
    conn.execute("INSERT INTO entity VALUES('e2','p1','character','陈默','[]',NULL,'active')")
    conn.execute("INSERT INTO entity VALUES('e3','p1','item','旧剑','[]',NULL,'archived')")

    conn.execute(
        "INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,entity_id,kind,"
        "payload_json,status,confidence,created_at,reviewed_at,reviewed_by) "
        "VALUES('cm1','p1','b1','s1','e2','人物',?,'accepted',0.9,?,?,'本地作者')",
        (json.dumps({"known_by": ["沈砚"], "private": True}, ensure_ascii=False), T, T),
    )

    # 事实矛盾：同一实体、同一结构化谓词、重叠有效期、两个取值。
    add_assertion(conn, "a1", "p1", "b1", "e1", "located_in", {"value": "青云城"})
    add_assertion(conn, "a2", "p1", "b1", "e1", "located_in", {"value": "西域"})
    # 干净事实。
    add_assertion(conn, "a3", "p1", "b1", "e1", "has_story_fact",
                  {"name": "沈砚", "type": "人物", "note": "主角"})
    # 知识越界：陈默相信自己不知道的事。
    add_assertion(conn, "a4", "p1", "b1", "e2", "has_story_fact",
                  {"name": "陈默", "type": "人物", "note": "剑客"},
                  epistemic="character_belief",
                  evidence=[{"candidate_memory_id": "cm1"}])
    # 来源失效：stale span。
    add_assertion(conn, "a5", "p1", "b1", "e1", "owns_item", {"value": "旧剑"},
                  evidence=[{"source_span_id": "s2"}])
    # 来源失效：旧 revision。
    add_assertion(conn, "a6", "p1", "b1", "e1", "age", {"value": 20},
                  evidence=[{"source_span_id": "s3"}])
    # 时间线：有效期起点晚于终点。
    add_assertion(conn, "a7", "p1", "b1", "e1", "birth", {"value": "戊戌年"},
                  valid_start="2026-05-01", valid_end="2026-01-01")
    # 局部认知被提升为世界事实（对象与 a3 相同，不触发事实矛盾）。
    add_assertion(conn, "a8", "p1", "b1", "e1", "has_story_fact",
                  {"name": "沈砚", "type": "人物", "note": "主角"},
                  evidence=[{"candidate_memory_id": "cm1"}])

    conn.execute(
        "INSERT INTO character_state VALUES('cs1','p1','b1','e1','性格',?,NULL,'2020-01-01',NULL,'{}',NULL)",
        (json.dumps({"value": "沉稳"}, ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO character_state VALUES('cs2','p1','b1','e1','性格',?,NULL,'2021-01-01',NULL,'{}',NULL)",
        (json.dumps({"value": "暴躁"}, ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO character_state VALUES('cs3','p1','b1','e1','年龄',?,NULL,'2020-01-01',NULL,'{}',NULL)",
        (json.dumps({"value": 20}, ensure_ascii=False),),
    )

    for rid, source, target, kind in (
        ("rel1", "e1", "e3", "主仆"),
        ("rel2", "e1", "e1", "心魔"),
        ("rel3", "e1", "e2", "师徒"),
        ("rel4", "e1", "e2", "师徒"),
    ):
        conn.execute(
            "INSERT INTO app_story_relation VALUES(?,?,?,?,?,?,'','active',?,?,'{}')",
            (rid, "p1", "b1", source, target, kind, T, T),
        )

    for eid, title, chapter, event_time, ordinal in (
        ("t1", "初入青云", "n5", "第三年", 5),
        ("t2", "西域来信", "n6", "第四年", 5),
        ("t3", "雨夜", "n7", "第五年", 7),
        ("t4", "雨夜之后", "n7", "第六年", 8),
        ("t5", "旧案", "n99", "第七年", 9),
    ):
        conn.execute(
            "INSERT INTO app_timeline_event VALUES(?,?,?,?,?,?,'[]','','planned',?,?,?,'{}')",
            (eid, "p1", "b1", title, event_time, chapter, ordinal, T, T),
        )

    for pid, stage, text in (
        ("p1", "setup", "断剑"),
        ("p2", "payoff", "旧王密信"),
        ("p3", "develop", "雨夜约定"),
        ("p4", "payoff", "无记录回收"),
        ("p5", "setup", "未链接伏笔"),
    ):
        conn.execute(
            "INSERT INTO app_plot_item VALUES(?,?,?,?,?,?,?,0)", (pid, "p1", "b1", stage, text, T, T)
        )

    for fid, plot, chapter, kind in (
        ("f1", "p1", "n1", "plant"),
        ("f2", "p2", "n10", "payoff"),
        ("f3", "p3", "n18", "plant"),
    ):
        conn.execute(
            "INSERT INTO app_foreshadow_link VALUES(?,?,?,?,?,?,'',?,?,'{}')",
            (fid, "p1", "b1", plot, chapter, kind, T, T),
        )


def seed_clean(conn) -> None:
    add_project(conn, "p2", "b2")
    # document_node.id 是全表主键，干净项目必须用自己的节点前缀，否则和 p1 撞车。
    add_chapters(conn, "p2", "b2", 3, prefix="c")
    add_revision(conn, "c1", "cr1", "b2", "少年推开木门。")
    add_span(conn, "cs1", "p2", "cr1", "c1", "少年推开木门。")
    conn.execute("INSERT INTO entity VALUES('ce1','p2','character','少年','[]',NULL,'active')")
    conn.execute(
        "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
        "epistemic_mode,state,confidence,evidence_json,reviewed_by,reviewed_at) "
        "VALUES('ca1','p2','b2','ce1','has_story_fact',?,'world_truth','canon',1.0,?,'本地作者',?)",
        (json.dumps({"name": "少年", "type": "人物", "note": "主角"}, ensure_ascii=False),
         json.dumps([{"source_span_id": "cs1"}], ensure_ascii=False), T),
    )


def categories_of(report) -> dict[str, int]:
    return {name: item["count"] for name, item in report["categories"].items() if item["count"]}


def main() -> None:
    conn = setup()
    seed_dirty(conn)
    report = analyze(conn, "p1", "b1")

    assert report["engine"] == "moyu-consistency"
    assert report["project_id"] == "p1" and report["branch_id"] == "b1"
    assert 0 <= report["score"] < 100, report["score"]
    assert report["grade"] in {"B", "C", "D"}, report["grade"]

    counts = categories_of(report)
    expected = {
        "fact": 1,
        "timeline": 4,
        "knowledge": 2,
        "attribute": 1,
        "relation": 3,
        "foreshadow": 4,
        "evidence": 2,
    }
    assert counts == expected, counts

    ids = [item["id"] for item in report["findings"]]
    assert len(ids) == len(set(ids)), "finding ids must be unique"
    assert ids == [item["id"] for item in analyze(conn, "p1", "b1")["findings"]], "must be deterministic"
    assert report["findings"] == sorted(
        report["findings"], key=lambda item: (SEVERITY_ORDER[item["severity"]], item["category"], item["id"])
    ), "findings must be sorted by severity"
    for item in report["findings"]:
        for field in ("id", "category", "category_label", "severity", "title", "detail", "evidence", "suggestion"):
            assert field in item and item[field] not in (None, ""), (field, item)
        assert item["severity"] in SEVERITY_ORDER, item

    # 每类问题的关键证据必须能回到具体记录。
    fact = next(item for item in report["findings"] if item["category"] == "fact")
    assert set(fact["evidence"]["assertion_ids"]) == {"a1", "a2"}
    evidence_finding = next(
        item for item in report["findings"]
        if item["category"] == "evidence" and item["severity"] == "high"
    )
    assert evidence_finding["evidence"]["source_span_id"] == "s2"

    # min_severity 过滤只影响返回清单，不影响检查计数。
    high_only = analyze(conn, "p1", "b1", min_severity="high")
    assert all(item["severity"] in {"blocker", "high"} for item in high_only["findings"])
    assert high_only["total_examined"] == report["total_examined"]
    # 筛选只裁剪清单，不允许把体检报告美化成分数 100。
    assert high_only["score"] == report["score"], (high_only["score"], report["score"])
    assert high_only["counts"] == report["counts"]
    assert high_only["returned"] < len(report["findings"])
    assert high_only["filtered_by"] == "high"
    assert report["filtered_by"] is None and report["returned"] == len(report["findings"])

    text = render_text(report)
    assert "一致性分数" in text and "问题清单" in text and "伏笔断线" in text

    # 干净项目必须真的存在：早期版本漏调 seed_clean，analyze 对着不存在的项目
    # 返回 100 分 A，让「干净基线」这条断言一直假通过（P7 修复后暴露）。
    seed_clean(conn)
    clean = analyze(conn, "p2", "b2")
    assert clean["score"] == 100.0, clean["score"]
    assert clean["grade"] == "A"
    assert clean["findings"] == [], clean["findings"]
    assert "未发现矛盾" in clean["summary"]

    # 数据损坏与等价取值（P11/P12/P13 的回归）：
    # - 单条坏 JSON 不能因为凑不出配对就静默漏报；
    # - 一条坏 + 两条合法只能报一次，不能按配对数重复；
    # - 1 与 1.0 是同一个取值，不能误报 high 并阻断发表。
    add_project(conn, "p3", "b3")
    add_chapters(conn, "p3", "b3", 3, prefix="m")
    conn.execute("INSERT INTO entity VALUES('e9','p3','character','顾寒','[]',NULL,'active')")
    conn.execute(
        "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
        "epistemic_mode,state,valid_start,valid_end,confidence,supersedes_id,evidence_json,"
        "reviewed_by,reviewed_at) VALUES('bad1','p3','b3','e9','age','{不是 JSON',"
        "'world_truth','canon',NULL,NULL,1.0,NULL,'[{\"source_span_id\": \"s1\"}]','本地作者',?)",
        (T,),
    )
    add_assertion(conn, "num1", "p3", "b3", "e9", "level", {"value": 1})
    add_assertion(conn, "num2", "p3", "b3", "e9", "level", {"value": 1.0})
    add_assertion(conn, "bad2", "p3", "b3", "e9", "level2", {"value": 2})
    conn.execute(
        "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
        "epistemic_mode,state,valid_start,valid_end,confidence,supersedes_id,evidence_json,"
        "reviewed_by,reviewed_at) VALUES('bad3','p3','b3','e9','level2','{坏',"
        "'world_truth','canon',NULL,NULL,1.0,NULL,'[{\"source_span_id\": \"s1\"}]','本地作者',?)",
        (T,),
    )
    broken = analyze(conn, "p3", "b3")
    broken_json = [item for item in broken["findings"] if item["title"] == "断言取值不是合法 JSON"]
    assert len(broken_json) == 2, [item["title"] for item in broken["findings"]]
    assert {item["evidence"]["assertion_id"] for item in broken_json} == {"bad1", "bad3"}
    assert all(item["severity"] == "medium" for item in broken_json)
    assert broken["score"] < 100, broken["score"]
    # 1 与 1.0 等价：不应产生 level 的冲突 finding。
    assert not [item for item in broken["findings"] if "level 存在" in item["title"]]

    # 大整数不能因为转 float 丢精度或溢出（P17 回归）。
    from backend.moyu_consistency import _same_value

    assert _same_value({"n": 1}, {"n": 1.0})
    assert not _same_value({"n": 2 ** 53}, {"n": 2 ** 53 + 1})
    assert _same_value({"n": 10 ** 400}, {"n": 10 ** 400})

    # t24 回归：同一角色的多条**不同**事实不是矛盾。
    # 旧语义按 (entity, predicate) 分组后比较 note，10 条事实两两配对 = C(10,2) = 45 条 low，
    # 扣 45 分把一份没有任何矛盾的稿子打成 D 级；新语义按 (entity, predicate, attribute) 分组，
    # 且 has_story_fact 只在「名字漂移」或「同一属性两个取值」时报问题。
    from backend.moyu_consistency import _attribute_slot, _classify_object_conflict

    add_project(conn, "p4", "b4")
    add_chapters(conn, "p4", "b4", 2, prefix="q")
    add_revision(conn, "q1", "qr1", "b4", "沈砚站在码头。")
    add_span(conn, "qspan", "p4", "qr1", "q1", "沈砚站在码头。")
    conn.execute("INSERT INTO entity VALUES('e10','p4','character','沈砚','[]',NULL,'active')")
    facts = [
        "出身寒门", "左手有旧伤", "怕水", "会看潮汐表", "欠船老大三块钱",
        "母亲早逝", "在码头长大", "不信官府", "藏着一封旧信", "说话慢",
    ]
    for index, note in enumerate(facts):
        add_assertion(
            conn, f"f{index}", "p4", "b4", "e10", "has_story_fact",
            {"name": "沈砚", "type": "人物", "note": note},
            evidence=[{"source_span_id": "qspan"}],
        )
    # 旧语义下这些事实会构成多少对：45（写死这个数字，防止有人「修」回旧语义后仍然绿灯）。
    assert len(facts) * (len(facts) - 1) // 2 == 45
    facts_only = analyze(conn, "p4", "b4")
    assert facts_only["findings"] == [], [item["title"] for item in facts_only["findings"]]
    assert facts_only["score"] == 100.0, facts_only["score"]
    assert facts_only["grade"] == "A", facts_only["grade"]

    # note 不同不再是矛盾（直接锁分类函数，避免只靠端到端计数间接覆盖）。
    assert _classify_object_conflict(
        "has_story_fact",
        {"name": "沈砚", "type": "人物", "note": "怕水"},
        {"name": "沈砚", "type": "人物", "note": "会看潮汐表"},
    ) == (None, "", ""), "两条不同事实描述不同是常态，不能判成矛盾"
    # 名字漂移仍然是 high（真冲突检测没被削掉）。
    drift_severity, drift_title, drift_detail = _classify_object_conflict(
        "has_story_fact", {"name": "沈砚"}, {"name": "沈石"}
    )
    assert drift_severity == "high", (drift_severity, drift_title)
    assert "名字" in drift_title and "沈砚" in drift_detail and "沈石" in drift_detail

    # 不同属性槽位天然不需要互相比对。
    assert _attribute_slot({"attribute": "  怕水  ", "value": 1}) == "怕水"
    assert _attribute_slot({"name": "沈砚"}) == ""
    assert _attribute_slot(None) == ""
    assert _classify_object_conflict(
        "has_story_fact",
        {"name": "沈砚", "attribute": "birth_city", "value": "临安"},
        {"name": "沈砚", "attribute": "fear_of", "value": "水"},
    ) == (None, "", "")
    add_assertion(
        conn, "f10", "p4", "b4", "e10", "has_story_fact",
        {"name": "沈砚", "attribute": "birth_city", "value": "临安"},
        evidence=[{"source_span_id": "qspan"}],
    )
    add_assertion(
        conn, "f11", "p4", "b4", "e10", "has_story_fact",
        {"name": "沈砚", "attribute": "fear_of", "value": "水"},
        evidence=[{"source_span_id": "qspan"}],
    )
    two_slots = analyze(conn, "p4", "b4")
    assert two_slots["findings"] == [], [item["title"] for item in two_slots["findings"]]

    # 同一属性、同一取值、不同描述：仍然不是矛盾。
    add_assertion(
        conn, "f12", "p4", "b4", "e10", "has_story_fact",
        {"name": "沈砚", "attribute": "birth_city", "value": "临安", "note": "在海宁出生"},
        evidence=[{"source_span_id": "qspan"}],
    )
    same_slot_same_value = analyze(conn, "p4", "b4")
    assert same_slot_same_value["findings"] == [], [
        item["title"] for item in same_slot_same_value["findings"]
    ]

    # 同一属性两个不同取值：仍然 high，且标题/detail 指出属性和两侧取值。
    add_assertion(
        conn, "f13", "p4", "b4", "e10", "has_story_fact",
        {"name": "沈砚", "attribute": "birth_city", "value": "余杭"},
        evidence=[{"source_span_id": "qspan"}],
    )
    conflict = analyze(conn, "p4", "b4")
    titles = [item["title"] for item in conflict["findings"]]
    assert titles == ["属性「birth_city」存在两个不同取值"] * 2, titles
    assert all(item["severity"] == "high" for item in conflict["findings"])
    assert all(item["category"] == "fact" for item in conflict["findings"])
    assert all(item["evidence"]["attribute"] == "birth_city" for item in conflict["findings"])
    assert {tuple(sorted(item["evidence"]["assertion_ids"])) for item in conflict["findings"]} == {
        ("f10", "f13"),
        ("f12", "f13"),
    }
    assert all("临安" in item["detail"] and "余杭" in item["detail"] for item in conflict["findings"])
    assert conflict["score"] == 84.0, conflict["score"]  # 2 × high(8 分)
    assert conflict["grade"] == "B", conflict["grade"]

    # 身份漂移在脏库层面也必须报（同一实体、同一谓词、无属性槽位）。
    add_project(conn, "p5", "b5")
    add_chapters(conn, "p5", "b5", 1, prefix="r")
    add_revision(conn, "r1", "rr1", "b5", "沈砚自称沈石。")
    add_span(conn, "rspan", "p5", "rr1", "r1", "沈砚自称沈石。")
    conn.execute("INSERT INTO entity VALUES('e11','p5','character','沈砚','[]',NULL,'active')")
    add_assertion(conn, "g1", "p5", "b5", "e11", "has_story_fact",
                  {"name": "沈砚", "type": "人物", "note": "主角"},
                  evidence=[{"source_span_id": "rspan"}])
    add_assertion(conn, "g2", "p5", "b5", "e11", "has_story_fact",
                  {"name": "沈石", "type": "人物", "note": "主角"},
                  evidence=[{"source_span_id": "rspan"}])
    drift_report = analyze(conn, "p5", "b5")
    assert [item["severity"] for item in drift_report["findings"]] == ["high"], [
        (item["title"], item["severity"]) for item in drift_report["findings"]
    ]
    assert "名字" in drift_report["findings"][0]["title"]

    # t25 回归：带 attribute 槽位的谓词（t23 写入端用的 has_attribute）按槽位判定。
    # 旧逻辑对非 has_story_fact 谓词一上来就报「两个取值即冲突」，5 个不同槽位 → 1 条 high、score 92/B。
    add_project(conn, "p6", "b6")
    add_chapters(conn, "p6", "b6", 1, prefix="s")
    add_revision(conn, "s1", "sr1", "b6", "沈砚家在青石镇。")
    add_span(conn, "sspan", "p6", "sr1", "s1", "沈砚家在青石镇。")
    conn.execute("INSERT INTO entity VALUES('e12','p6','character','沈砚','[]',NULL,'active')")
    slots = [
        ("home_city", "青石镇", "家乡"),
        ("age", "二十七", "年龄"),
        ("weapon", "短刀", "随身兵器"),
        ("rank", "船工", "身份"),
        ("health", "左臂有旧伤", "身体状况"),
    ]
    for index, (attribute, value, note) in enumerate(slots):
        add_assertion(
            conn, f"h{index}", "p6", "b6", "e12", "has_attribute",
            {"name": "沈砚", "type": "人物", "note": note,
             "attribute": attribute, "value": value},
            evidence=[{"source_span_id": "sspan"}],
        )
    attributes_only = analyze(conn, "p6", "b6")
    assert attributes_only["findings"] == [], [
        (item["title"], item["detail"]) for item in attributes_only["findings"]
    ]
    assert attributes_only["score"] == 100.0, attributes_only["score"]
    assert attributes_only["grade"] == "A", attributes_only["grade"]

    # 直接锁分类函数：不同槽位不比较，单值谓词仍然比较。
    assert _classify_object_conflict(
        "has_attribute",
        {"name": "沈砚", "attribute": "home_city", "value": "青石镇"},
        {"name": "沈砚", "attribute": "age", "value": "二十七"},
    ) == (None, "", ""), "不同属性槽位不是同一个事实，不能判冲突"
    assert _classify_object_conflict("located_in", {"value": "青云城"}, {"value": "西域"})[0] == "high"
    assert _classify_object_conflict("located_at", {"value": "码头"}, {"value": "县衙"})[0] == "high"
    # 数据形状决定语义：谓词名换成从未见过的新名字，只要带槽位就按槽位判定。
    assert _classify_object_conflict(
        "future_predicate",
        {"attribute": "home_city", "value": "青石镇"},
        {"attribute": "age", "value": "二十七"},
    ) == (None, "", "")

    # 同槽位不同取值：恰好 1 条 high，标题带槽位名，detail 带两侧取值。
    add_assertion(
        conn, "h5", "p6", "b6", "e12", "has_attribute",
        {"name": "沈砚", "type": "人物", "note": "迁移后",
         "attribute": "home_city", "value": "临安"},
        evidence=[{"source_span_id": "sspan"}],
    )
    one_conflict = analyze(conn, "p6", "b6")
    assert len(one_conflict["findings"]) == 1, [
        (item["title"], item["detail"]) for item in one_conflict["findings"]
    ]
    only = one_conflict["findings"][0]
    assert only["severity"] == "high", only["severity"]
    assert "home_city" in only["title"], only["title"]
    assert "青石镇" in only["detail"] and "临安" in only["detail"], only["detail"]
    assert only["category"] == "fact", only["category"]
    assert one_conflict["score"] == 92.0, one_conflict["score"]  # 1 × high(8 分)
    assert one_conflict["grade"] == "B", one_conflict["grade"]

    # 同槽位相同 value、描述不同：不是矛盾。
    add_project(conn, "p7", "b7")
    add_chapters(conn, "p7", "b7", 1, prefix="t")
    add_revision(conn, "t1", "tr1", "b7", "沈砚家在青石镇。")
    add_span(conn, "tspan", "p7", "tr1", "t1", "沈砚家在青石镇。")
    conn.execute("INSERT INTO entity VALUES('e13','p7','character','沈砚','[]',NULL,'active')")
    add_assertion(
        conn, "k1", "p7", "b7", "e13", "has_attribute",
        {"name": "沈砚", "type": "人物", "note": "档案一",
         "attribute": "home_city", "value": "青石镇"},
        evidence=[{"source_span_id": "tspan"}],
    )
    add_assertion(
        conn, "k2", "p7", "b7", "e13", "has_attribute",
        {"name": "沈砚", "type": "人物", "note": "档案二（描述不同）",
         "attribute": "home_city", "value": "青石镇"},
        evidence=[{"source_span_id": "tspan"}],
    )
    same_value_report = analyze(conn, "p7", "b7")
    assert same_value_report["findings"] == [], [
        (item["title"], item["detail"]) for item in same_value_report["findings"]
    ]
    assert same_value_report["score"] == 100.0 and same_value_report["grade"] == "A"

    # t42 回归：库里历史行——`has_attribute` 的 object 没有 attribute 键（写入端 t41 之前放行过）。
    # 两侧都没有槽位时无法确定在比哪一条属性，必须跳过配对；旧行为会把它们判成 high 假阳。
    add_project(conn, "p8", "b8")
    add_chapters(conn, "p8", "b8", 1, prefix="u")
    add_revision(conn, "u1", "ur1", "b8", "林昭左臂有疤，惯用右手。")
    add_span(conn, "uspan", "p8", "ur1", "u1", "林昭左臂有疤，惯用右手。")
    conn.execute("INSERT INTO entity VALUES('e14','p8','character','林昭','[]',NULL,'active')")
    for identifier, note in (("h8a", "左臂有疤"), ("h8b", "惯用右手"), ("h8c", "会使短刀")):
        add_assertion(
            conn, identifier, "p8", "b8", "e14", "has_attribute",
            {"name": "林昭", "type": "character", "note": note, "predicate": "has_attribute"},
            evidence=[{"source_span_id": "uspan"}],
        )
    # 同一项目里再挂一对无槽位的单值谓词：它必须仍判 high，不能被这次放宽误伤。
    add_assertion(
        conn, "h8d", "p8", "b8", "e14", "located_in", {"value": "青云城"},
        evidence=[{"source_span_id": "uspan"}],
    )
    add_assertion(
        conn, "h8e", "p8", "b8", "e14", "located_in", {"value": "西域"},
        evidence=[{"source_span_id": "uspan"}],
    )
    legacy_report = analyze(conn, "p8", "b8")
    legacy_fact = [item for item in legacy_report["findings"] if item["category"] == "fact"]
    assert [(item["severity"], item["title"]) for item in legacy_fact] == [
        ("high", "located_in 存在冲突取值")
    ], [(item["title"], item["detail"]) for item in legacy_report["findings"]]
    assert all("has_attribute" not in item["title"] for item in legacy_report["findings"])
    assert legacy_report["score"] == 92.0, legacy_report["score"]  # 1 × high(8 分)
    assert legacy_report["grade"] == "B", legacy_report["grade"]

    conn.close()
    print("consistency fixtures: OK", {"dirty": report["score"], "clean": clean["score"]})


if __name__ == "__main__":
    main()
