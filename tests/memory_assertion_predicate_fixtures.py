#!/usr/bin/env python3
"""记忆写入断言时不再丢 predicate/attribute/value。

覆盖三件事：
1. `ensure_memory_assertion()` 的写入端：带 attribute 的记忆落 `has_attribute` 并把
   `attribute`/`value` 原样写进 `object_json`；没有 attribute 的记忆保持旧三键形状；
   白名单外的谓词不落库、回落并留一条事件；canon 触发器前置条件不被绕过。
2. `reuse_analysis_candidate()` 的读取端：带 attribute 的候选能复用（不误判 conflict），
   且断言里的 attribute/value 与候选一致；被改过的槽位必须拒绝复用。
3. 旧路径向后兼容：事实类候选的 predicate 仍是 `has_story_fact`、object 仍是三键。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

os.environ.setdefault("MOYU_AI_BASE_URL", "")

import analysis_candidate_typed_http_fixtures as typed
from backend import moyu_consistency as consistency
from backend import server


MEMORY_ID = "cm_test"
ENTITY_ID = "ent_test"
SPAN_ID = "span_test"


class MemoryAssertionWrite(unittest.TestCase):
    """写入端：直接调 ensure_memory_assertion，只看它往 assertion 表写了什么。"""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.saved_db = server.DB_PATH
        server.DB_PATH = Path(self.root.name) / "book.sqlite3"
        self.addCleanup(setattr, server, "DB_PATH", self.saved_db)
        server.init_db()
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status)"
                " VALUES(?,?,?,?,?,?)",
                (ENTITY_ID, server.project_id(), "人物", "林岚", "[]", "active"),
            )

    def write(self, memory):
        with server.connect() as conn:
            assertion_id = server.ensure_memory_assertion(
                conn, MEMORY_ID, memory, ENTITY_ID, SPAN_ID)
            row = conn.execute(
                "SELECT * FROM assertion WHERE id=?", (assertion_id,)).fetchone() if assertion_id else None
            events = [] if assertion_id is None else [
                dict(item) for item in conn.execute(
                    "SELECT * FROM event_log WHERE aggregate_id=? AND event_type=?",
                    (MEMORY_ID, server.ASSERTION_PREDICATE_FALLBACK_EVENT))]
        return assertion_id, row, events

    def test_attribute_memory_writes_structural_slot(self):
        value = {"伤": "旧伤", "程度": 2, "部位": ["左手", "手腕"]}
        memory = {
            "status": "accepted", "name": "林岚", "type": "人物", "note": "左手有旧伤",
            "attribute": "左手", "value": value, "epistemic_mode": "world_truth",
        }
        assertion_id, row, events = self.write(memory)
        self.assertIsNotNone(assertion_id, "带 attribute 的记忆应当写入 canon 断言")
        self.assertEqual(row["predicate"], "has_attribute")
        self.assertEqual(row["state"], "canon")
        self.assertEqual(events, [], "结构化谓词是白名单内取值，不该记回落事件")
        payload = json.loads(row["object_json"])
        self.assertEqual(payload["attribute"], "左手")
        self.assertEqual(payload["value"], value, "value 必须原样保留结构")
        self.assertIsInstance(payload["value"], dict, "value 不许被字符串化")
        self.assertIsInstance(payload["value"]["部位"], list)
        self.assertEqual(payload["value"]["程度"], 2)
        self.assertEqual((payload["name"], payload["type"], payload["note"]),
                         ("林岚", "人物", "左手有旧伤"), "旧三键必须保留，读取端依赖它")

    def test_plain_memory_keeps_legacy_object(self):
        assertion_id, row, events = self.write({
            "status": "accepted", "name": "林岚", "type": "人物", "note": "出身寒门",
        })
        self.assertIsNotNone(assertion_id)
        self.assertEqual(row["predicate"], "has_story_fact")
        self.assertEqual(json.loads(row["object_json"]),
                         {"name": "林岚", "type": "人物", "note": "出身寒门"},
                         "没有 attribute 时 object_json 必须与今天逐字节同形")
        self.assertEqual(events, [])

    def test_unknown_predicate_falls_back_and_records_event(self):
        assertion_id, row, events = self.write({
            "status": "accepted", "name": "林岚", "type": "人物", "note": "喜怒无常",
            "predicate": "性格；不是白名单谓词",
        })
        self.assertIsNotNone(assertion_id)
        self.assertEqual(row["predicate"], "has_story_fact", "白名单外的谓词不许直接落库")
        self.assertEqual(len(events), 1, "回落必须留一条可审计的事件")
        payload = json.loads(events[0]["payload_json"])
        self.assertEqual(payload["requested"], "性格；不是白名单谓词")
        self.assertEqual(payload["used"], "has_story_fact")
        self.assertEqual(events[0]["aggregate_id"], MEMORY_ID)

    def test_unknown_predicate_with_attribute_uses_structural_default(self):
        assertion_id, row, events = self.write({
            "status": "accepted", "name": "林岚", "type": "人物", "note": "怕水",
            "predicate": "害怕", "attribute": "恐惧", "value": "水",
        })
        self.assertIsNotNone(assertion_id)
        self.assertEqual(row["predicate"], "has_attribute",
                         "有 attribute 时回落目标是结构化默认值，不能被模型自由文本决定")
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]["payload_json"])["used"], "has_attribute")
        self.assertEqual(json.loads(row["object_json"])["value"], "水")

    def test_whitelisted_predicate_is_kept_verbatim(self):
        for requested in sorted(server.ASSERTION_PREDICATES):
            with self.subTest(predicate=requested):
                memory = {"status": "accepted", "name": "林岚", "type": "人物",
                          "note": "断言" + requested, "predicate": requested}
                if requested == "has_attribute":
                    memory.update({"attribute": "手", "value": "旧伤"})
                assertion_id, row, events = self.write(memory)
                self.assertIsNotNone(assertion_id)
                self.assertEqual(row["predicate"], requested)
                self.assertEqual(events, [])

    def test_canon_prerequisites_are_untouched(self):
        _, row, _ = self.write({
            "status": "accepted", "name": "林岚", "type": "人物", "note": "会看潮汐表",
            "epistemic_mode": "不存在的模式", "valid_start": "1", "valid_end": "9",
            "attribute": "技能", "value": ["潮汐"],
        })
        self.assertEqual(row["state"], "canon")
        evidence = json.loads(row["evidence_json"])
        self.assertTrue(isinstance(evidence, list) and evidence, "evidence_json 必须是非空数组")
        self.assertEqual(evidence[0], {"candidate_memory_id": MEMORY_ID, "source_span_id": SPAN_ID})
        self.assertTrue(row["reviewed_by"], "canon 触发器要求 reviewed_by")
        self.assertTrue(row["reviewed_at"], "canon 触发器要求 reviewed_at")
        self.assertEqual(row["epistemic_mode"], "world_truth", "未知 epistemic_mode 仍回落默认值")
        self.assertEqual((row["valid_start"], row["valid_end"]), ("1", "9"))

    def test_gating_paths_write_nothing(self):
        cases = {
            "status": ({"status": "待确认", "name": "林岚", "note": "x"}, ENTITY_ID, SPAN_ID),
            "entity": ({"status": "accepted", "name": "林岚", "note": "x"}, None, SPAN_ID),
            "span": ({"status": "accepted", "name": "林岚", "note": "x"}, ENTITY_ID, None),
        }
        with server.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]
        for label, (memory, entity_id, span_id) in cases.items():
            with self.subTest(gate=label):
                with server.connect() as conn:
                    self.assertIsNone(server.ensure_memory_assertion(
                        conn, MEMORY_ID, memory, entity_id, span_id))
        with server.connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]
        self.assertEqual(after, before)

    def test_reanchored_assertion_is_updated_with_the_slot(self):
        plain = {"status": "accepted", "name": "林岚", "type": "人物", "note": "左手有旧伤"}
        first, _, _ = self.write(plain)
        with_attribute = {**plain, "attribute": "左手", "value": "旧伤"}
        second, row, _ = self.write(with_attribute)
        self.assertEqual(second, first, "同一记忆重锚应复用同一条断言")
        self.assertEqual(row["predicate"], "has_attribute", "重锚时谓词也要跟着记忆走")
        self.assertEqual(json.loads(row["object_json"])["attribute"], "左手")

    def test_slotless_has_attribute_falls_back_to_the_fact_bucket(self):
        """白名单内也不等于可以空放行：没有槽位的 has_attribute 会造出假阳。"""
        assertion_id, row, events = self.write({
            "status": "accepted", "name": "林岚", "type": "人物", "note": "左臂有疤",
            "predicate": "has_attribute",
        })
        self.assertIsNotNone(assertion_id)
        self.assertEqual(row["predicate"], "has_story_fact",
                         "没有 attribute 槽位时不许落 has_attribute")
        payload = json.loads(row["object_json"])
        self.assertEqual(sorted(payload), ["name", "note", "type"],
                         "落库 object 仍是旧三键，不许出现空槽位")
        self.assertEqual(len(events), 1, "回落必须留一条可审计的事件")
        rejected = json.loads(events[0]["payload_json"])
        self.assertEqual(rejected["requested"], "has_attribute")
        self.assertEqual(rejected["used"], "has_story_fact")
        self.assertIn("槽位", rejected["reason"],
                      "回落原因要写明是缺槽位，别让审计误读成模型乱写谓词")

    def test_blank_has_attribute_slot_falls_back_to_the_fact_bucket(self):
        assertion_id, row, events = self.write({
            "status": "accepted", "name": "林岚", "type": "人物", "note": "惯用右手",
            "predicate": "has_attribute", "attribute": "   ", "value": "右",
        })
        self.assertIsNotNone(assertion_id)
        self.assertEqual(row["predicate"], "has_story_fact",
                         "纯空白槽位等于没有槽位")
        self.assertEqual(sorted(json.loads(row["object_json"])), ["name", "note", "type"])
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]["payload_json"])["requested"], "has_attribute")

    def test_predicate_matrix(self):
        """五类情形的返回值矩阵必须与改动前逐条一致。"""
        cases = {
            "白名单内且带槽位": (
                {"predicate": "has_attribute", "attribute": "home_city", "value": "青石镇"},
                ("has_attribute", None)),
            "白名单内缺槽位": ({"predicate": "has_attribute"},
                             ("has_story_fact", "has_attribute")),
            "无谓词有槽位": ({"attribute": "home_city", "value": "青石镇"},
                          ("has_attribute", None)),
            "无谓词无槽位": ({}, ("has_story_fact", None)),
            "白名单外无槽位": ({"predicate": "located_at"},
                          ("has_story_fact", "located_at")),
            "白名单外有槽位": ({"predicate": "located_at", "attribute": "位置"},
                          ("has_attribute", "located_at")),
        }
        for label, (memory, expected) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(server.assertion_predicate(memory), expected)
                object_json = server.assertion_object(memory)
                self.assertEqual(sorted(object_json),
                                 ["name", "note", "type"]
                                 if server.assertion_predicate(memory)[0] == "has_story_fact"
                                 else ["attribute", "name", "note", "type"]
                                 if "value" not in memory
                                 else ["attribute", "name", "note", "type", "value"])


class MemoryAssertionReuse(typed.AnalysisCandidateTypedHTTP):
    """读取端：带 attribute 的候选必须能复用，不能被 object_json 形状变化打断。"""

    def assertion_of(self, accepted):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT * FROM assertion WHERE id=? AND project_id=?",
                (accepted["artifacts"]["assertion_id"], typed.PROJECT)).fetchone()
        self.assertIsNotNone(row)
        return row

    def test_character_state_candidate_reuse_keeps_attribute_slot(self):
        raw = self.raw("character_state")
        first, accepted = self.accept(raw)
        candidate = accepted
        row = self.assertion_of(accepted)
        self.assertEqual(row["predicate"], "has_attribute")
        payload = json.loads(row["object_json"])
        self.assertEqual(payload["attribute"], candidate["attribute"])
        self.assertEqual(payload["value"], candidate["value"])
        self.assertEqual((payload["name"], payload["type"], payload["note"]),
                         (candidate["name"], candidate["type"], candidate["note"]))

        second = self.renew(raw, first)
        before, calls = self.canonical(), len(typed.Provider.requests)
        status, response = self.confirm(second)
        current = (response.get("analysis") or {}).get("candidates", [{}])[0]
        self.assertEqual(status, 200, response)
        self.assertEqual(typed.reuse.artifact_ids(current), typed.reuse.artifact_ids(accepted))
        self.assertEqual(self.canonical(), before, "复用不得改动正式资料")
        self.assertEqual(len(typed.Provider.requests), calls)
        self.assertIsNotNone(self.assertion_of(accepted))

    def test_character_state_reuse_rejects_a_changed_slot(self):
        raw = self.raw("character_state")
        first, accepted = self.accept(raw)
        corrupted = {"name": "林岚", "type": "人物", "note": "左手有旧伤",
                     "attribute": "左手", "value": {"伤": "痊愈", "部位": ["左手"]}}
        self.mutate_sql(
            "changed_character_state_assertion_slot",
            "UPDATE assertion SET object_json=? WHERE id=? AND project_id=?",
            (server.json_text(corrupted), accepted["artifacts"]["assertion_id"], typed.PROJECT),
        )
        second = self.renew(raw, first)
        self.assert_rejected_atomically(second, "synthetic_character_state_slot")

    def test_fact_candidate_reuse_still_uses_the_legacy_shape(self):
        raw = self.raw("fact")
        first, accepted = self.accept(raw)
        candidate = accepted
        row = self.assertion_of(accepted)
        self.assertEqual(row["predicate"], "has_story_fact")
        self.assertEqual(json.loads(row["object_json"]),
                         {"name": candidate["name"], "type": candidate["type"],
                          "note": candidate["note"]})
        second = self.renew(raw, first)
        status, response = self.confirm(second)
        current = (response.get("analysis") or {}).get("candidates", [{}])[0]
        self.assertEqual(status, 200, response)
        self.assertEqual(typed.reuse.artifact_ids(current), typed.reuse.artifact_ids(accepted))

    def fact_payload(self, quote, note):
        """按指定原句构造一条 fact 候选，并把它伪装成「只写 has_attribute、不给槽位」。"""
        start = self.fixture_body.index(quote)
        raw = self.raw("fact")
        evidence = {"source_chapter_id": self.chapter_id, "source_quote": quote,
                    "source_start": start, "source_end": start + len(quote)}
        candidate = dict(raw["candidates"][0])
        candidate.update(evidence)
        candidate.update({"note": note, "predicate": "has_attribute"})
        return {
            "summary": {**raw["summary"], "evidence": [evidence]},
            "handoff": {**raw["handoff"], "evidence": [evidence]},
            "candidates": [candidate],
        }

    def test_slotless_has_attribute_candidate_lands_in_the_fact_bucket(self):
        """端到端：只写 has_attribute 不给槽位的候选，采纳后必须是事实桶且不再假阳。

        修前：两条这样的断言都落 `has_attribute`、object 只有三键，引擎按
        `(实体, has_attribute, 空槽位)` 分组后把它们判成 high 取值冲突——
        两个不同事实被当成矛盾，正是 t23/t24 修掉的那类假阳从白名单缝里漏回来。
        """
        first_raw = self.fact_payload("林岚把左手指尖抵在铜制窗扣上", "左臂有疤")
        _, first = self.accept(first_raw)
        second_raw = self.fact_payload("同行的周闻站在北窗外", "惯用右手")
        _, second = self.accept(second_raw)

        self.assertNotEqual(first["artifacts"]["memory_id"], second["artifacts"]["memory_id"],
                            "两条不同事实必须是两条记忆")
        assertion_ids = [first["artifacts"]["assertion_id"], second["artifacts"]["assertion_id"]]
        memory_ids = [first["artifacts"]["memory_id"], second["artifacts"]["memory_id"]]
        with server.connect() as conn:
            for assertion_id in assertion_ids:
                row = conn.execute(
                    "SELECT predicate,object_json,state FROM assertion WHERE id=? AND project_id=?",
                    (assertion_id, typed.PROJECT)).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["predicate"], "has_story_fact",
                                 "写入端不许把无槽位候选落成 has_attribute")
                self.assertEqual(row["state"], "canon")
                self.assertEqual(sorted(json.loads(row["object_json"])),
                                 ["name", "note", "type"],
                                 "落库 object 不许出现空槽位")
            for memory_id in memory_ids:
                events = conn.execute(
                    "SELECT payload_json FROM event_log WHERE aggregate_id=? AND event_type=?",
                    (memory_id, server.ASSERTION_PREDICATE_FALLBACK_EVENT)).fetchall()
                self.assertEqual(len(events), 1, "每条回落都要有事件")
                payload = json.loads(events[0]["payload_json"])
                self.assertEqual((payload["requested"], payload["used"]),
                                 ("has_attribute", "has_story_fact"))
            report = consistency.analyze(conn, typed.PROJECT, server.branch_id())
        forged = [item for item in report["findings"]
                  if item["severity"] == "high" and item["category"] == "fact"
                  and "has_attribute" in item["title"]]
        self.assertEqual(forged, [],
                         "两条无槽位断言不许再被判成 has_attribute 取值冲突")


if __name__ == "__main__":
    unittest.main(verbosity=2)
