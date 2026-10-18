#!/usr/bin/env python3
"""Run actual retrieval, source validation and review contracts on fixed prose.

The labels name passages in the generated corpus. Predictions are obtained
from assemble_context, including its final budget pass, never supplied by the
fixture. These are lexical retrieval checks, not model or literary scores.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.moyu_context import (  # noqa: E402
    ScopePolicy, analysis_request, assemble_context, chapter_chunks, paragraph_ranges,
    refresh_analysis_status, scope_decision, search_materials, text_hash,
    validate_chapter_analysis, validate_source_quote, validate_story_review,
)


def corpus(size: int) -> tuple[dict, dict, dict, list[dict]]:
    records = [
        {"id": "lan", "type": "人物", "name": "林岚", "aliases": ["阿岚", "小岚"], "description": "她在渡口长大。"},
        {"id": "keeper", "type": "人物", "name": "守灯人", "aliases": ["塔伯"], "description": "守灯人负责夜航信号。"},
        {"id": "villain", "type": "人物", "name": "铁鹭", "aliases": ["老鹭"], "description": "铁鹭声称自己只是巡夜人。"},
    ]
    passages = [
        {"chapter": 3, "quote": "王嬷嬷把冷灰药粉塞进袖口，叮嘱林岚别让它沾水。", "query": "让林岚检查袖口的冷灰药粉是否还能使用。"},
        {"chapter": 7, "quote": "林岚摸到月牙密钥藏在左靴内侧，钥齿缺了半角。", "query": "寻找月牙密钥，记住它藏在什么地方。"},
        {"chapter": 9, "quote": "林岚的左肩在渡桥时被木刺划伤，守灯人给她缠上窄布。", "query": "让林岚再次抬起左肩时想起渡桥留下的伤。"},
        {"chapter": 13, "quote": "林岚答应在子夜把红铜罗盘交给守灯人，此后她不再绕过北塔。", "query": "阿岚要兑现把红铜罗盘交给塔伯的约定。"},
        {"chapter": 15, "quote": "门闩要先向外提两寸再向右转，否则铜簧会惊动看守。", "query": "这次开门仍要照门闩向外提两寸再向右转的办法。"},
    ]
    lookup = {item["chapter"]: item["quote"] for item in passages}
    chapters = []
    for number in range(1, size + 1):
        # Each labelled sentence lies beyond an old head/tail clipping window.
        before = "夜潮推过石阶，栈桥下的木桩发出迟缓的响声。林岚停在屋檐边数着远处的灯。" * 48
        after = "巡夜的人沿着空巷走过，雨水顺瓦沟滴进竹筒。她没有急着开口，先等脚步转入街角。" * 62
        middle = lookup.get(number, f"第{number}次查验时，巡夜簿留下了平常的一页。")
        chapters.append({
            "id": f"chapter-{number}", "title": f"第{number}章 夜航",
            "body": before + "\n\n" + middle + "\n\n" + after, "status": "完成",
            "project_id": "fixture-book", "branch_id": "main",
        })
    current = chapters[size - 3]
    current.update({"body": "林岚走到北塔门外，等待守灯人答应开门。", "pov": "阿岚", "chapter_goal": "进入北塔"})
    future = chapters[size - 2]
    secret = "真凶铁鹭就是林岚的父亲，暗号一共藏了十七年。"
    future["body"] = secret
    revisions = {item["id"]: f"revision-{item['id']}" for item in chapters}
    future_memory = {
        "id": "future-memory", "type": "fact", "name": "真凶", "note": secret,
        "status": "accepted", "source_chapter_id": future["id"],
        "source_revision_id": revisions[future["id"]], "current_revision_id": revisions[future["id"]],
        "source_quote": secret, "source_start": 0, "source_end": len(secret),
        "source_hash": text_hash(secret), "source_span_id": "span-future", "source_span_status": "valid",
    }
    valid_quote = passages[1]["quote"]
    valid_chapter = chapters[6]
    left = valid_chapter["body"].index(valid_quote)
    valid_memory = {
        "id": "valid-memory", "type": "fact", "name": "月牙密钥",
        "status": "accepted", "source_chapter_id": valid_chapter["id"],
        "source_revision_id": revisions[valid_chapter["id"]], "current_revision_id": revisions[valid_chapter["id"]],
        "source_quote": valid_quote, "source_start": left, "source_end": left + len(valid_quote),
        "source_hash": text_hash(valid_quote), "source_span_id": "span-key", "source_span_status": "valid",
        "known_by": ["lan"],
    }
    state = {
        "project": {"id": "fixture-book", "title": "隔离检索样本", "summary": "夜航小城里的追索", "goal": 100000},
        "branch_id": "main", "chapters": chapters, "records": records,
        "memory": [future_memory, valid_memory],
        "plot": {"setup": [], "develop": [], "payoff": []},
    }
    structure = {
        "relationships": [{"id": "foreign-relation", "project_id": "other-book", "note": "跨书关系"}],
        "timeline": [
            {"id": "future-time", "chapter_id": future["id"], "summary": secret, "status": "occurred"},
            {"id": "future-plan", "chapter_id": future["id"], "summary": secret, "status": "planned"},
            {"id": "past-event", "chapter_id": "chapter-3", "summary": passages[0]["quote"], "status": "occurred"},
        ],
        "foreshadowing": [],
        "character_states": [
            {"id": "villain-only", "attribute": "秘密", "value": secret, "valid_start": "chapter-1", "knowledge_scope": {"entities": ["villain"]}},
            {"id": "foreign-state", "branch_id": "alternate", "attribute": "秘密", "value": "异分支"},
            {"id": "expired-state", "attribute": "伤口", "value": "痊愈前", "valid_end": "chapter-5"},
            {"id": "valid-state", "entity_id": "lan", "attribute": "左肩", "value": "有伤", "valid_start": "chapter-9", "knowledge_scope": {"characters": ["lan"]}},
        ],
        "plot_threads": [], "conflicts": [],
    }
    return state, structure, revisions, passages


BENCHMARK_RESULTS: list[dict] = []


class ContextPipelineTests(unittest.TestCase):
    def test_actual_30_100_300_chapter_retrieval(self):
        for size in (30, 100, 300):
            state, structure, revisions, labels = corpus(size)
            misses = []
            illegal = []
            durations = []
            for label in labels:
                started = time.perf_counter()
                context, current = assemble_context(
                    state, state["chapters"][size - 3]["id"], structure=structure,
                    instruction=label["query"], revisions=revisions,
                    profile={"chapter_top_k": 5, "recent_window": 1, "memory_top_k": 3, "record_top_k": 5},
                )
                durations.append(round(time.perf_counter() - started, 4))
                actual = [
                    item for item in context["retrieved_chapters"]
                    if item["id"] == f"chapter-{label['chapter']}" and label["quote"] in item["excerpt"]
                ]
                if not actual:
                    misses.append(label["quote"])
                else:
                    evidence = actual[0]["evidence"]
                    body = state["chapters"][label["chapter"] - 1]["body"]
                    span = evidence["source_range"]
                    self.assertEqual(body[span["start"]:span["end"]], actual[0]["excerpt"])
                    self.assertEqual(evidence["source_revision_id"], revisions[actual[0]["id"]])
                    self.assertEqual(evidence["source_hash"], text_hash(actual[0]["excerpt"]))
                    self.assertEqual(evidence["chapter_hash"], text_hash(body))
                transmitted = json.dumps(context, ensure_ascii=False)
                if "真凶铁鹭就是林岚的父亲" in transmitted:
                    illegal.append("future_or_private_quote")
                for collection in ("retrieved_chapters", "previous_chapters"):
                    if any(item["chapter"] >= current["chapter_number"] for item in context[collection]):
                        illegal.append("future_chapter")
                ids = {item["id"] for values in context["story_structure"].values() for item in values}
                if ids.intersection({"villain-only", "foreign-state", "expired-state", "future-time", "foreign-relation"}):
                    illegal.append("scope_leak")
                self.assertNotIn("future-memory", {item["id"] for item in context["confirmed_memory"]})
                self.assertTrue(context["context_budget"]["author_instruction_used"])
                self.assertFalse(context["context_budget"]["token_count_exact"])
                self.assertIsNone(context["context_budget"]["actual_input_tokens"])
                self.assertLessEqual(context["context_budget"]["estimated_tokens"], context["context_budget"]["max_tokens"])
            row = {
                "chapters": size, "labelled_queries": len(labels),
                "actual_passage_recall": (len(labels) - len(misses)) / len(labels),
                "missed_passages": misses, "illegal_material_count": len(illegal),
                "illegal_materials": illegal, "context_build_seconds": durations,
                "scope": "actual assemble_context output after budget; local lexical retrieval; fixed synthetic prose; no model",
            }
            BENCHMARK_RESULTS.append(row)
            self.assertEqual(misses, [], row)
            self.assertEqual(illegal, [], row)

    def test_instruction_changes_actual_ranked_material(self):
        state, structure, revisions, labels = corpus(30)
        results = []
        for label in labels[1:3]:
            context, _ = assemble_context(
                state, "chapter-28", structure=structure, revisions=revisions,
                instruction=label["query"], profile={"chapter_top_k": 1, "recent_window": 0},
            )
            results.append(context["retrieved_chapters"][0]["id"])
        self.assertEqual(results, ["chapter-7", "chapter-9"])

    def test_small_budget_preserves_current_revision_and_exact_body(self):
        body = "林岚握住铜匙。\n门外的潮声低了。"
        state = {
            "project": {"id": "book", "title": "夜航", "summary": "", "genre": ""},
            "branch_id": "main", "chapters": [
                {"id": "current", "title": "第一章", "body": body, "status": "草稿", "chapter_goal": ""},
            ], "records": [], "memory": [], "plot": {},
        }
        context, _ = assemble_context(
            state, "current", task="polish", revisions={"current": "current-revision"},
            profile={"max_tokens": 4000, "chapter_top_k": 0, "recent_window": 0, "memory_top_k": 0, "record_top_k": 0},
        )
        current = context["current_chapter"]
        self.assertEqual(current["body"], body)
        self.assertEqual(current["source_range"], {"start": 0, "end": len(body)})
        self.assertEqual(current["source_revision_id"], "current-revision")
        self.assertEqual(current["source_hash"], text_hash(body))
        self.assertTrue(current["full_content"])
        rendered = json.dumps(context, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        self.assertEqual(context["context_budget"]["estimated_utf8_bytes"], len(rendered.encode()))
        self.assertLessEqual(context["context_budget"]["estimated_tokens"], 4000)
        self.assertFalse(context["context_budget"]["token_count_exact"])

    def test_zero_limits_and_author_pinning(self):
        state, structure, revisions, _ = corpus(30)
        context, _ = assemble_context(
            state, "chapter-28", structure=structure, revisions=revisions,
            profile={"chapter_top_k": 0, "recent_window": 0, "memory_top_k": 0, "record_top_k": 0},
        )
        for field in ("retrieved_chapters", "previous_chapters", "confirmed_memory", "story_cards"):
            self.assertEqual(context[field], [], field)
        omitted, _ = assemble_context(
            state, "chapter-28", revisions=revisions, instruction="月牙密钥",
            controls={"chapters": {"chapter-7": {"omit": True}}},
        )
        self.assertNotIn("chapter-7", {item["id"] for item in omitted["retrieved_chapters"] + omitted["previous_chapters"]})
        pinned, _ = assemble_context(
            state, "chapter-28", revisions=revisions, instruction="月牙密钥",
            profile={"chapter_top_k": 1, "max_tokens": 64_000},
            controls={"chapters": {"chapter-15": {"pin": True}}},
        )
        self.assertEqual(pinned["retrieved_chapters"][0]["id"], "chapter-15")
        self.assertTrue(pinned["retrieved_chapters"][0]["full_content"])
        self.assertEqual(pinned["retrieved_chapters"][0]["excerpt"], state["chapters"][14]["body"])
        for key, profile_name in (("chapters", "chapter_top_k"), ("memory", "memory_top_k"), ("records", "record_top_k")):
            with self.assertRaisesRegex(ValueError, "zero"):
                assemble_context(
                    state, "chapter-28", revisions=revisions,
                    profile={profile_name: 0}, controls={key: {"anything": {"pin": True}}},
                )
        current_pin, _ = assemble_context(
            state, "chapter-28", revisions=revisions, profile={"chapter_top_k": 0},
            controls={"chapters": {"chapter-28": {"pin": True, "order": 1}}},
        )
        self.assertEqual(current_pin["current_chapter"]["id"], "chapter-28")
        self.assertEqual(current_pin["retrieved_chapters"], [])
        with self.assertRaisesRegex(ValueError, "cannot be omitted"):
            assemble_context(state, "chapter-28", controls={"chapters": {"chapter-28": {"omit": True}}})

    def test_sources_reject_stale_wrong_and_ambiguous_quotes(self):
        chapter = {"id": "quote", "body": "门关上了。\n门关上了。\n林岚抬头。"}
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            validate_source_quote({"source_quote": "门关上了。"}, chapter, "r1")
        anchored = validate_source_quote({"source_quote": "门关上了。", "source_start": 0, "source_end": 5}, chapter, "r1")
        self.assertEqual(anchored["source_hash"], text_hash("门关上了。"))
        self.assertEqual(anchored["source_end_utf8"], 15)
        for bad in (
            {"source_quote": "并不存在的门"},
            {"source_quote": "林岚抬头。", "source_start": True, "source_end": 6},
            {"source_quote": "林岚抬头。", "source_revision_id": "old"},
            {"source_quote": "林岚抬头。", "source_hash": "wrong"},
        ):
            with self.assertRaises(ValueError):
                validate_source_quote(bad, chapter, "r1")

    def test_memory_revision_scope_and_evidence_are_checked(self):
        state, structure, revisions, _ = corpus(30)
        valid = state["memory"][1]
        stale = {**valid, "id": "stale", "source_revision_id": "old"}
        false_quote = {**valid, "id": "false-quote", "source_quote": "不存在的既定事实"}
        absent_revision = {**valid, "id": "missing-revision"}
        absent_revision.pop("source_revision_id")
        unconfirmed = {**valid, "id": "unconfirmed", "status": "candidate"}
        state["memory"].extend([stale, false_quote, absent_revision, unconfirmed])
        context, _ = assemble_context(state, "chapter-28", structure=structure, revisions=revisions)
        self.assertEqual([item["id"] for item in context["confirmed_memory"]], ["valid-memory"])
        filtered = {item["id"]: item["reasons"] for item in context["context_budget"]["knowledge_scope"]["filtered"]}
        self.assertIn("stale_revision", filtered["stale"])
        self.assertIn("invalid_source_quote", filtered["false-quote"])
        self.assertIn("missing_source_revision", filtered["missing-revision"])
        self.assertIn("unconfirmed_memory", filtered["unconfirmed"])

    def test_body_scope_and_alias_ambiguity(self):
        state = {
            "project": {"id": "book", "title": "范围"},
            "branch_id": "main",
            "records": [
                {"id": "lan", "name": "林岚", "aliases": ["小林"]},
                {"id": "lin-other", "name": "林远", "aliases": ["小林"]},
            ],
            "chapters": [
                {"id": "past", "body": "铁鹭在门后藏了银针。", "source_scopes": [{"start": 0, "end": 11, "scope": {"known_by": ["villain"]}}]},
                {"id": "current", "body": "林岚走向那扇门。", "pov": "林岚"},
            ],
        }
        context, _ = assemble_context(state, "current", instruction="门后银针")
        self.assertEqual(context["retrieved_chapters"], [])
        self.assertEqual(context["previous_chapters"], [])
        ambiguous, _ = assemble_context(state, "current", instruction="小林的过去")
        self.assertEqual(ambiguous["context_budget"]["ambiguous_aliases"][0]["entity_ids"], ["lan", "lin-other"])

    def test_knowledge_scope_intersects_owner_declarations(self):
        policy = ScopePolicy("book", "main", 2, pov_entity_ids=("lan",))
        cases = [
            {"known_by": ["lan", "villain"], "knowledge_scope": {"known_by": ["villain"]}},
            {"known_by": ["villain"], "knowledge_scope": {"known_by": ["lan", "villain"]}},
            {"known_by": ["lan"], "knowledge_scope": {"known_by": [], "private": True}},
            {"knowledge_scope": {"known_by": ["lan"], "characters": ["villain"]}},
            {"knowledge_scope": {"characters": ["lan"], "entities": ["villain"]}},
        ]
        for item in cases:
            with self.subTest(item=item):
                self.assertIn("pov_knowledge", scope_decision(item, policy, kind="chapter")["reasons"])
        legacy = scope_decision({"known_by": []}, policy, kind="chapter")
        self.assertTrue(legacy["allowed"])
        self.assertIn("pov_knowledge_unannotated", legacy["warnings"])
        scoped = {"known_by": [], "knowledge_scope": {"known_by": ["lan"], "private": True}}
        self.assertTrue(scope_decision(scoped, policy, kind="chapter")["allowed"])

    def test_scene_permissions_narrow_chapter_knowledge(self):
        public = "林岚沿着门外的石阶走过。\n"
        secret = "铁鹭在密室藏了银针，门后留有暗号。"
        past = {
            "id": "past", "body": public + secret,
            "known_by": ["lan", "villain"],
            "knowledge_scope": {"known_by": ["lan", "villain"]},
            "source_scopes": [{"start": len(public), "end": len(public + secret),
                               "scope": {"known_by": ["villain"]}}],
        }
        state = {
            "project": {"id": "book"}, "branch_id": "main",
            "records": [{"id": "lan", "name": "林岚"}],
            "chapters": [past, {"id": "current", "body": "林岚走向门外。", "pov": "林岚"}],
        }
        policy = ScopePolicy("book", "main", 2, pov_entity_ids=("lan",))
        chunks = chapter_chunks(past, "r1")
        self.assertTrue(scope_decision(chunks[0], policy, kind="chapter")["allowed"])
        self.assertEqual(chunks[1]["known_by"], ["villain"])
        self.assertIn("pov_knowledge", scope_decision(chunks[1], policy, kind="chapter")["reasons"])
        context, _ = assemble_context(state, "current", instruction="密室银针暗号")
        self.assertNotIn(secret, json.dumps(context, ensure_ascii=False))
        for name in ("retrieved_chapters", "previous_chapters"):
            self.assertTrue(all(secret not in item["excerpt"] for item in context[name]))
        with self.assertRaisesRegex(ValueError, "outside the current viewpoint knowledge"):
            assemble_context(state, "current", controls={"chapters": {"past": {"pin": True}}})
        pinned, _ = assemble_context(
            state, "current",
            controls={"chapters": {"past": {"pin": True, "range": {"start": 0, "end": len(public)}}}},
        )
        self.assertEqual(pinned["retrieved_chapters"][0]["excerpt"], public)
        self.assertEqual(past["known_by"], ["lan", "villain"])
        self.assertEqual(past["knowledge_scope"]["known_by"], ["lan", "villain"])
        narrower_chapter = {
            **past, "known_by": ["villain"],
            "source_scopes": [{"start": len(public), "end": len(public + secret),
                               "scope": {"known_by": ["lan", "villain"]}}],
        }
        narrowed = chapter_chunks(narrower_chapter, "r1")[1]
        self.assertEqual(narrowed["known_by"], ["villain"])
        self.assertFalse(scope_decision(narrowed, policy, kind="chapter")["allowed"])

    def test_mutually_exclusive_scene_owners_remain_closed(self):
        body = "林岚看见窗边的旧信。铁鹭在门后藏了银针。"
        boundary = body.index("铁鹭")
        chapter = {
            "id": "past", "body": body, "known_by": ["lan", "villain"],
            "knowledge_scope": {"characters": ["lan", "villain"]},
            "source_scopes": [
                {"start": 0, "end": boundary, "scope": {"known_by": ["lan"]}},
                {"start": boundary, "end": len(body), "scope": {"known_by": ["villain"]}},
            ],
        }
        chunk = chapter_chunks(chapter, "r1")[0]
        self.assertEqual(chunk["known_by"], [])
        self.assertEqual(chunk["knowledge_scope"]["known_by"], [])
        self.assertTrue(chunk["knowledge_scope"]["private"])
        for pov in ("lan", "villain"):
            policy = ScopePolicy("book", "main", 2, pov_entity_ids=(pov,))
            self.assertIn("pov_knowledge", scope_decision(chunk, policy, kind="chapter")["reasons"])
            state = {
                "project": {"id": "book"}, "branch_id": "main",
                "chapters": [chapter, {"id": "current", "body": "门外传来脚步。", "pov_entity_ids": [pov]}],
            }
            context, _ = assemble_context(state, "current", instruction="旧信银针")
            self.assertEqual(context["retrieved_chapters"], [])
            self.assertEqual(context["previous_chapters"], [])
            self.assertNotIn(body, json.dumps(context, ensure_ascii=False))
        omniscient = ScopePolicy("book", "main", 2, narrator_mode="omniscient")
        self.assertTrue(scope_decision(chunk, omniscient, kind="chapter")["allowed"])

    def test_private_chapter_and_ownerless_scene_remain_closed(self):
        policy = ScopePolicy("book", "main", 2, pov_entity_ids=("lan",))
        private_chapter = chapter_chunks({"id": "private", "body": "无人在场时留下的秘密。", "private": True})[0]
        self.assertIn("unbounded_private_material", scope_decision(private_chapter, policy, kind="chapter")["reasons"])
        scene = chapter_chunks({
            "id": "past", "body": "无人知晓的暗号。", "known_by": ["lan"],
            "source_scopes": [{"start": 0, "end": 9, "scope": {"private": True}}],
        })[0]
        self.assertEqual(scene["known_by"], [])
        self.assertFalse(scope_decision(scene, policy, kind="chapter")["allowed"])
        private_with_scene = chapter_chunks({
            "id": "private-scene", "body": "无人知晓的暗号。", "private": True,
            "source_scopes": [{"start": 0, "end": 9, "scope": {"known_by": ["lan"]}}],
        })[0]
        self.assertEqual(private_with_scene["known_by"], [])
        self.assertFalse(scope_decision(private_with_scene, policy, kind="chapter")["allowed"])

    def test_story_time_belief_and_plans(self):
        policy = ScopePolicy("book", "main", 10, "0010-02-02", ("lan",))
        cases = [
            ({"id": "future-time", "event_time": "0010-02-03"}, "future_story_time"),
            ({"id": "future-start", "valid_start": "chapter-11"}, "future_state"),
            ({"id": "wrong-belief", "epistemic_mode": "character_belief", "entity_id": "villain"}, "other_character_belief"),
            ({"id": "wrong-pov", "known_by": ["villain"]}, "pov_knowledge"),
            ({"id": "future-plan", "epistemic_mode": "author_plan"}, "author_plan"),
        ]
        for item, reason in cases:
            self.assertIn(reason, scope_decision(item, policy, kind="timeline")["reasons"])
        state, structure, revisions, _ = corpus(30)
        writing, _ = assemble_context(state, "chapter-28", structure=structure, revisions=revisions)
        planning, _ = assemble_context(state, "chapter-28", structure=structure, revisions=revisions, task="outline")
        self.assertEqual(writing["author_plans"], [])
        self.assertEqual(planning["author_plans"][0]["id"], "future-plan")
        self.assertEqual(planning["author_plans"][0]["use"], "planning_only_not_occurred")

    def test_complete_and_selected_chapter_budget(self):
        body = "章首不能丢。" + "林岚擦净铜盘上的潮气。" * 100
        state = {"project": {"id": "book"}, "chapters": [{"id": "long", "body": body}], "branch_id": "main"}
        context, _ = assemble_context(state, "long", task="polish", revisions={"long": "r1"})
        self.assertEqual(context["current_chapter"]["body"], body)
        self.assertTrue(context["current_chapter"]["full_content"])
        selected, _ = assemble_context(state, "long", task="polish", revisions={"long": "r1"}, selection={"range": {"start": 0, "end": 7}, "revision_id": "r1"})
        self.assertEqual(selected["current_chapter"]["body"], body[:7])
        self.assertFalse(selected["current_chapter"]["full_content"])
        with self.assertRaisesRegex(ValueError, "stale"):
            assemble_context(state, "long", selection={"range": {"start": 0, "end": 7}, "revision_id": "old"}, revisions={"long": "r1"})
        state["chapters"][0]["body"] *= 100
        with self.assertRaisesRegex(ValueError, "超出本次材料容量"):
            assemble_context(state, "long", task="polish")

    def test_paragraph_chunks_do_not_change_source(self):
        body = "短段落。\r\n\n" + "这一段很长，但是不能丢失任何字符。" * 100 + "\n末段。"
        ranges = paragraph_ranges(body)
        chunks = chapter_chunks({"id": "one", "body": body}, "r1")
        self.assertEqual(len(ranges), len(chunks))
        for chunk in chunks:
            self.assertLessEqual(len(chunk["body"]), 900)
            self.assertEqual(body[chunk["source_start"]:chunk["source_end"]], chunk["source_quote"])
            self.assertEqual(chunk["source_revision_id"], "r1")

    def test_shared_search_scope(self):
        materials = [
            {"id": "visible", "kind": "setting", "name": "月牙密钥", "body": "铜色钥齿"},
            {"id": "hidden", "kind": "setting", "name": "月牙密钥", "body": "同词", "known_by": ["villain"]},
            {"id": "other", "kind": "setting", "name": "月牙密钥", "body": "跨书", "project_id": "other"},
        ]
        actual = search_materials(materials, "月牙密钥", ScopePolicy("book", "main", 3, pov_entity_ids=("lan",)))
        self.assertEqual([item["id"] for item in actual["results"]], ["visible"])
        self.assertEqual(len(actual["excluded"]), 2)
        self.assertEqual(search_materials(materials, "月牙密钥", ScopePolicy("book", "main"), limit=0)["results"], [])

    def test_ordinary_prose_analysis_dedup_and_staleness(self):
        body = "林岚把钥匙交给守灯人。\n守灯人答应在子夜回来。\n她的左肩仍然疼痛。"
        chapter = {"id": "analysis", "body": body}
        raw = {
            "summary": {"text": "林岚交出钥匙，约好子夜见面。", "evidence": [{"source_quote": "林岚把钥匙交给守灯人。"}, {"source_quote": "守灯人答应在子夜回来。"}]},
            "handoff": {"text": "等待子夜守灯人回来。", "evidence": [{"source_quote": "守灯人答应在子夜回来。"}]},
            "candidates": [
                {"kind": "event", "name": "交出钥匙", "note": "林岚把钥匙交给守灯人。", "source_quote": "林岚把钥匙交给守灯人。", "epistemic_mode": "world_truth", "known_by": ["lan", "keeper"]},
                {"kind": "relationship", "name": "子夜约定", "note": "守灯人答应回来。", "subject_name": "守灯人", "target_name": "林岚", "predicate": "约定", "source_quote": "守灯人答应在子夜回来。", "epistemic_mode": "world_truth", "known_by": ["lan", "keeper"]},
                {"kind": "character_state", "name": "左肩疼痛", "note": "左肩尚未恢复。", "subject_name": "林岚", "attribute": "左肩", "value": "疼痛", "source_quote": "她的左肩仍然疼痛。", "epistemic_mode": "world_truth", "known_by": ["lan"]},
            ],
        }
        raw["candidates"].append(copy.deepcopy(raw["candidates"][0]))
        first = validate_chapter_analysis(raw, chapter, project_id="book", branch_id="main", revision_id="r1", records=[{"id": "lan", "name": "林岚"}, {"id": "keeper", "name": "守灯人"}])
        second = validate_chapter_analysis(raw, chapter, project_id="book", branch_id="main", revision_id="r1", records=[{"id": "lan", "name": "林岚"}, {"id": "keeper", "name": "守灯人"}])
        self.assertEqual(len(first["candidates"]), 3)
        self.assertEqual(first, second)
        self.assertTrue(all(item["status"] == "candidate" and item["review_required"] for item in first["candidates"]))
        stale = refresh_analysis_status(first, {**chapter, "body": "改稿。"}, "r2")
        self.assertEqual(stale["status"], "stale")
        self.assertTrue(stale["handoff"]["stale"])
        self.assertTrue(all(item["stale"] for item in stale["candidates"]))
        self.assertEqual(first["status"], "pending")
        request = analysis_request(chapter, project_id="book", branch_id="main", revision_id="r1")
        self.assertEqual(request["schema"]["required"], ["summary", "handoff", "candidates"])
        fabricated = copy.deepcopy(raw)
        fabricated["candidates"][0]["source_quote"] = "林岚其实没交出钥匙。"
        with self.assertRaisesRegex(ValueError, "absent"):
            validate_chapter_analysis(fabricated, chapter, project_id="book", branch_id="main", revision_id="r1")
        no_evidence = copy.deepcopy(raw)
        no_evidence["summary"]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "evidence"):
            validate_chapter_analysis(no_evidence, chapter, project_id="book", branch_id="main", revision_id="r1")

    def test_semantic_review_requires_both_quotes_and_author_decision(self):
        chapters = {
            "old": {"id": "old", "body": "林岚把唯一的钥匙交了出去。"},
            "now": {"id": "now", "body": "林岚取出那把钥匙打开了门。"},
        }
        raw = {
            "findings": [{"category": "continuity", "severity": "contradiction", "message": "需解释钥匙如何回到她手中。", "evidence": [
                {"source_chapter_id": "old", "source_quote": chapters["old"]["body"]},
                {"source_chapter_id": "now", "source_quote": chapters["now"]["body"]},
            ]}],
            "uncertainties": ["也可能是未提供的中间情节解释了变化。"],
        }
        report = validate_story_review(raw, chapters, {"old": "r1", "now": "r2"}, project_id="book", branch_id="main", current_chapter_id="now")
        self.assertFalse(report["auto_pass"])
        self.assertFalse(report["literary_quality_verified"])
        self.assertEqual(report["findings"][0]["status"], "pending")
        raw["findings"][0]["evidence"].pop()
        with self.assertRaisesRegex(ValueError, "both"):
            validate_story_review(raw, chapters, {"old": "r1", "now": "r2"}, project_id="book", branch_id="main", current_chapter_id="now")


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ContextPipelineTests))
    print(json.dumps({"benchmark": BENCHMARK_RESULTS, "passed": outcome.wasSuccessful()}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
