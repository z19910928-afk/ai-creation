#!/usr/bin/env python3
"""Intersect saved material boundaries in local contexts and request previews."""
from __future__ import annotations

from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_provider
import moyu_records
import server
from moyu_context import ScopePolicy, scope_decision


class ScopeIntersectionTests(unittest.TestCase):
    def setUp(self):
        self.policy = ScopePolicy("book", "main", 2, pov_entity_ids=("hero",))

    def test_each_chapter_declaration_constrains_both_directions(self):
        for field, loose, strict, reason in (
                ("available_from", "1", "3", "future_reveal"),
                ("valid_start", "1", "3", "future_state"),
                ("valid_end", "10", "1", "expired_state")):
            for outer, inner in ((loose, strict), (strict, loose)):
                with self.subTest(field=field, outer=outer, inner=inner):
                    item = {field: outer, "knowledge_scope": {field: inner}}
                    before = copy.deepcopy(item)
                    decision = scope_decision(item, self.policy, kind="setting")
                    self.assertFalse(decision["allowed"])
                    self.assertIn(reason, decision["reasons"])
                    self.assertEqual(item, before)

    def test_intersection_includes_exact_boundaries_and_all_fields(self):
        item = {
            "available_from": "1", "valid_start": "2", "valid_end": "2",
            "knowledge_scope": {
                "available_from": "第2章", "valid_start": "chapter-1", "valid_end": "10",
            },
        }
        self.assertTrue(scope_decision(item, self.policy)["allowed"])
        item["knowledge_scope"].update(available_from="3", valid_start="3", valid_end="1")
        self.assertEqual(set(scope_decision(item, self.policy)["reasons"]),
                         {"future_reveal", "future_state", "expired_state"})

    def test_json_and_legacy_scope_aliases_keep_both_boundaries(self):
        for field, nested in (
                ("knowledge_scope_json", json.dumps({"valid_start": "3"})),
                ("scope", {"valid_start": "3"})):
            with self.subTest(field=field):
                decision = scope_decision({"valid_start": "1", field: nested}, self.policy)
                self.assertIn("future_state", decision["reasons"])

    def test_author_plan_in_either_declaration_stays_a_plan(self):
        for outer, inner in (("author_plan", "world_truth"), ("world_truth", "author_plan")):
            with self.subTest(outer=outer):
                decision = scope_decision({
                    "epistemic_mode": outer, "knowledge_scope": {"epistemic_mode": inner},
                }, self.policy)
                self.assertFalse(decision["allowed"])
                self.assertTrue(decision["is_plan"])
                self.assertEqual(decision["epistemic_mode"], "author_plan")
                self.assertIn("author_plan", decision["reasons"])

    def test_character_belief_in_either_declaration_requires_viewpoint_knowledge(self):
        for outer, inner in (("character_belief", "world_truth"), ("world_truth", "character_belief")):
            with self.subTest(outer=outer):
                item = {"entity_id": "other", "epistemic_mode": outer,
                        "knowledge_scope": {"epistemic_mode": inner}}
                decision = scope_decision(item, self.policy, kind="character_state")
                self.assertFalse(decision["allowed"])
                self.assertEqual(decision["epistemic_mode"], "character_belief")
                self.assertIn("other_character_belief", decision["reasons"])
                item["entity_id"] = "hero"
                self.assertTrue(scope_decision(item, self.policy, kind="character_state")["allowed"])
                item.update(entity_id="other", known_by=["hero"])
                self.assertTrue(scope_decision(item, self.policy, kind="character_state")["allowed"])
                item["known_by"] = ["other"]
                self.assertIn("pov_knowledge", scope_decision(item, self.policy)["reasons"])

    def test_plan_permission_does_not_disable_belief_or_other_boundaries(self):
        policy = ScopePolicy("book", "main", 2, pov_entity_ids=("hero",), allow_plans=True)
        for outer, inner in (("author_plan", "character_belief"), ("character_belief", "author_plan")):
            with self.subTest(outer=outer):
                item = {"entity_id": "other", "epistemic_mode": outer,
                        "knowledge_scope": {"epistemic_mode": inner}}
                decision = scope_decision(item, policy)
                self.assertEqual(decision["epistemic_mode"], "author_plan")
                self.assertTrue(decision["is_plan"])
                self.assertIn("other_character_belief", decision["reasons"])
                self.assertNotIn("author_plan", decision["reasons"])
                item.update(entity_id="hero", valid_start="3")
                self.assertIn("future_state", scope_decision(item, policy)["reasons"])
                item.update(valid_start="1", branch_id="other")
                self.assertIn("branch_scope", scope_decision(item, policy)["reasons"])

    def test_invalid_mode_cannot_hide_behind_a_valid_declaration(self):
        for outer, inner in (("unknown", "world_truth"), ("world_truth", "unknown"),
                             ("author_plan", "unknown")):
            with self.subTest(outer=outer, inner=inner):
                decision = scope_decision({
                    "epistemic_mode": outer, "knowledge_scope": {"epistemic_mode": inner},
                }, self.policy)
                self.assertIn("invalid_epistemic_mode", decision["reasons"])

    def test_existing_namespace_and_private_owner_intersections_remain_strict(self):
        cases = (
            ({"project_id": "other", "knowledge_scope": {"project_id": "book"}}, "project_scope"),
            ({"project_id": "book", "knowledge_scope": {"project_id": "other"}}, "project_scope"),
            ({"branch_id": "other", "knowledge_scope": {"branch_id": "main"}}, "branch_scope"),
            ({"branch_id": "main", "knowledge_scope": {"branch_id": "other"}}, "branch_scope"),
            ({"known_by": ["hero"], "knowledge_scope": {"known_by": ["other"]}}, "pov_knowledge"),
            ({"known_by": ["other"], "knowledge_scope": {"characters": ["hero"]}}, "pov_knowledge"),
            ({"private": True, "knowledge_scope": {}}, "unbounded_private_material"),
            ({"knowledge_scope": {"private": True, "known_by": []}}, "pov_knowledge"),
        )
        for item, reason in cases:
            with self.subTest(item=item):
                self.assertIn(reason, scope_decision(item, self.policy)["reasons"])
        permitted = {"known_by": ["hero", "other"],
                     "knowledge_scope": {"known_by": ["hero"], "private": True}}
        self.assertTrue(scope_decision(permitted, self.policy)["allowed"])

    def test_unannotated_and_other_valid_modes_keep_existing_behavior(self):
        self.assertTrue(scope_decision({}, self.policy)["allowed"])
        for mode in ("world_truth", "narrator", "inference"):
            with self.subTest(mode=mode):
                decision = scope_decision({"epistemic_mode": mode}, self.policy)
                self.assertTrue(decision["allowed"])
                self.assertEqual(decision["epistemic_mode"], mode)
        omniscient = ScopePolicy("book", "main", 2, narrator_mode="omniscient")
        belief = {"entity_id": "other", "epistemic_mode": "world_truth",
                  "knowledge_scope": {"epistemic_mode": "character_belief"}}
        self.assertTrue(scope_decision(belief, omniscient)["allowed"])


class ActualContextIntersectionTests(unittest.TestCase):
    CARD_SECRET = "CARD_SCOPE_SECRET_37A"
    MEMORY_SECRET = "MEMORY_SCOPE_SECRET_46B"
    STATE_SECRET = "STATE_SCOPE_SECRET_55C"
    BELIEF_SECRET = "BELIEF_SCOPE_SECRET_64D"
    PROFILE = {"chapter_top_k": 0, "recent_window": 0, "memory_top_k": 60, "record_top_k": 40}

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="moyu-scope-intersection-")
        self.addCleanup(directory.cleanup)
        previous = server.DB_PATH
        self.addCleanup(setattr, server, "DB_PATH", previous)
        server.DB_PATH = Path(directory.name) / "synthetic.sqlite3"
        environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_SECRET_KEY": "", "MOYU_CREDENTIALS_DIR": str(Path(directory.name) / "keys"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        for owner, name in ((server, "call_ai_provider"), (server, "start_ai_worker"),
                            (moyu_provider, "call_chat_completion")):
            blocker = patch.object(owner, name, side_effect=AssertionError("external requests are forbidden"))
            mocked = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mocked.assert_not_called)
        server.init_db()
        with closing(server.connect()) as conn, conn:
            server.ensure_seed(conn)
            self.first = server.chapter_rows(conn)[0]["id"]
            server.update_chapter(conn, self.first, {"body": "旧书上写着" + self.MEMORY_SECRET + "。"})
            self.current = server.create_chapter(
                conn, {"id": "scope_current", "title": "第二章", "body": "林岚推开旧门。", "pov": "林岚"})
            server.create_chapter(conn, {"id": "scope_future", "title": "第三章", "body": "尚未发生。"})
            for identifier, name, kind, description in (
                    ("scope_hero", "林岚", "人物", "当前视角人物"),
                    ("scope_other", "铁鹭", "人物", "另一人物"),
                    ("scope_card", "旧门暗号", "设定", self.CARD_SECRET)):
                conn.execute(
                    "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status) "
                    "VALUES(?,?,?,?,?,'active')",
                    (identifier, server.project_id(), kind, name, "[]"))
                conn.execute("INSERT INTO app_entity_meta VALUES(?,?,?)",
                             (identifier, description, server.now()))
            self.memory_payload = {
                "name": "旧书证据", "note": self.MEMORY_SECRET, "source_chapter_id": self.first,
                "source_quote": self.MEMORY_SECRET, "known_by": ["scope_hero"],
                "epistemic_mode": "world_truth",
            }
            span = server.source_span_for_memory(conn, self.memory_payload)
            self.assertTrue(span)
            conn.execute(
                "INSERT INTO candidate_memory(id,project_id,branch_id,entity_id,source_span_id,kind,"
                "payload_json,status,confidence,created_at) VALUES(?,?,?,?,?,'fact',?,'accepted',1,?)",
                ("scope_memory", server.project_id(), server.branch_id(), "scope_hero", span,
                 server.json_text(self.memory_payload), server.now()))
            conn.execute(
                "INSERT INTO character_state(id,project_id,branch_id,entity_id,attribute,value_json,"
                "valid_start,valid_end,knowledge_scope_json) VALUES(?,?,?,?,?,?,?,?,?)",
                ("scope_state", server.project_id(), server.branch_id(), "scope_hero", "门后事实",
                 server.json_text(self.STATE_SECRET), "1", "10", '{"known_by":["scope_hero"]}'))

    def card_scope(self, declaration):
        with closing(server.connect()) as conn, conn:
            moyu_records.write_scope(conn, "scope_card", declaration, server.now())

    def memory_scope(self, declaration):
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id='scope_memory'",
                         (server.json_text({**self.memory_payload, **declaration}),))

    def state_scope(self, *, start="1", end="10", scope=None):
        with closing(server.connect()) as conn, conn:
            conn.execute(
                "UPDATE character_state SET valid_start=?,valid_end=?,knowledge_scope_json=? WHERE id='scope_state'",
                (start, end, server.json_text({"known_by": ["scope_hero"], **(scope or {})})))

    def inspect(self, task="continue", controls=None):
        with closing(server.connect()) as conn:
            before = tuple(conn.iterdump())
            context, chapter = server.ai_context(
                conn, self.current, retrieval_profile=self.PROFILE, material_controls=controls, task=task)
            self.assertEqual(chapter["id"], self.current)
            preview = server.ai_prompt_preview(
                task, context, "", config={"base_url": "http://127.0.0.1", "model": "offline", "api_key": ""})
            self.assertEqual(tuple(conn.iterdump()), before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_run").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_job").fetchone()[0], 0)
        self.assertEqual(context["retrieved_chapters"], [])
        self.assertEqual(context["previous_chapters"], [])
        self.assertEqual(preview["request"]["messages"], preview["messages"])
        return context, preview

    def assert_blocked(self, context, preview, identifier, marker, reason):
        material = [
            *context["story_cards"], *context["confirmed_memory"],
            *context["story_structure"]["character_states"], *context["author_plans"],
        ]
        self.assertNotIn(identifier, {item["id"] for item in material})
        self.assertNotIn(marker, server.json_text({key: value for key, value in context.items()
                                                  if not key.startswith("_")}))
        self.assertNotIn(marker, server.json_text(preview["request"]))
        row = next(item for item in context["context_budget"]["knowledge_scope"]["filtered"]
                   if item["id"] == identifier)
        self.assertIn(reason, row["reasons"])

    def test_baseline_material_reaches_real_context_and_prepared_messages_without_writes(self):
        context, preview = self.inspect()
        for key, identifier, marker in (
                ("story_cards", "scope_card", self.CARD_SECRET),
                ("confirmed_memory", "scope_memory", self.MEMORY_SECRET)):
            self.assertIn(identifier, {item["id"] for item in context[key]})
            self.assertIn(marker, server.json_text(preview["request"]))
        self.assertIn("scope_state", {item["id"] for item in context["story_structure"]["character_states"]})
        self.assertIn(self.STATE_SECRET, server.json_text(preview["request"]))

    def test_conflicting_card_chapter_bounds_never_enter_request_material(self):
        for field, loose, strict, reason in (
                ("available_from", "1", "3", "future_reveal"),
                ("valid_start", "1", "3", "future_state"),
                ("valid_end", "10", "1", "expired_state")):
            for outer, inner in ((loose, strict), (strict, loose)):
                with self.subTest(field=field, outer=outer):
                    self.card_scope({field: outer, "knowledge_scope": {field: inner}})
                    context, preview = self.inspect()
                    self.assert_blocked(context, preview, "scope_card", self.CARD_SECRET, reason)

    def test_accepted_memory_keeps_both_chapter_limits_and_exact_source_evidence(self):
        for field, loose, strict, reason in (
                ("valid_start", "1", "3", "future_state"),
                ("valid_end", "10", "1", "expired_state")):
            for outer, inner in ((loose, strict), (strict, loose)):
                with self.subTest(field=field, outer=outer):
                    self.memory_scope({field: outer, "knowledge_scope": {field: inner}})
                    context, preview = self.inspect()
                    self.assert_blocked(context, preview, "scope_memory", self.MEMORY_SECRET, reason)

    def test_character_state_keeps_column_and_json_chapter_limits(self):
        for field, loose, strict, reason in (
                ("valid_start", "1", "3", "future_state"),
                ("valid_end", "10", "1", "expired_state")):
            for outer, inner in ((loose, strict), (strict, loose)):
                with self.subTest(field=field, outer=outer):
                    self.state_scope(start=outer if field == "valid_start" else "1",
                                     end=outer if field == "valid_end" else "10", scope={field: inner})
                    context, preview = self.inspect()
                    self.assert_blocked(context, preview, "scope_state", self.STATE_SECRET, reason)

    def test_plan_in_either_layer_is_excluded_from_prose_and_labeled_for_planning(self):
        for outer, inner in (("world_truth", "author_plan"), ("author_plan", "world_truth")):
            with self.subTest(outer=outer):
                declaration = {"epistemic_mode": outer, "knowledge_scope": {"epistemic_mode": inner}}
                self.card_scope(declaration)
                self.memory_scope(declaration)
                context, preview = self.inspect()
                self.assert_blocked(context, preview, "scope_card", self.CARD_SECRET, "author_plan")
                self.assert_blocked(context, preview, "scope_memory", self.MEMORY_SECRET, "author_plan")
                planning, prepared = self.inspect("outline")
                for key in ("story_cards", "confirmed_memory"):
                    self.assertNotIn("scope_card", {item["id"] for item in planning[key]})
                    self.assertNotIn("scope_memory", {item["id"] for item in planning[key]})
                plans = {item["id"]: item for item in planning["author_plans"]}
                for identifier, marker in (("scope_card", self.CARD_SECRET),
                                           ("scope_memory", self.MEMORY_SECRET)):
                    self.assertEqual(plans[identifier]["epistemic_mode"], "author_plan")
                    self.assertEqual(plans[identifier]["use"], "planning_only_not_occurred")
                    self.assertIn(marker, server.json_text(prepared["request"]))

    def test_other_character_belief_cannot_hide_under_world_truth(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_entity_meta SET description=? WHERE entity_id='scope_other'",
                         (self.BELIEF_SECRET,))
        for outer, inner in (("world_truth", "character_belief"), ("character_belief", "world_truth")):
            with self.subTest(outer=outer):
                with closing(server.connect()) as conn, conn:
                    moyu_records.write_scope(conn, "scope_other", {
                        "epistemic_mode": outer,
                        "knowledge_scope": {"epistemic_mode": inner, "subject_entity_id": "scope_other"},
                    }, server.now())
                context, preview = self.inspect()
                self.assert_blocked(context, preview, "scope_other", self.BELIEF_SECRET,
                                    "other_character_belief")

    def test_pinning_cannot_override_a_hidden_stricter_declaration(self):
        self.card_scope({"valid_start": "1", "knowledge_scope": {"valid_start": "3"}})
        with self.assertRaisesRegex(ValueError, "outside the current knowledge scope"):
            self.inspect(controls={"records": {"scope_card": {"pin": True}}})
        self.memory_scope({"valid_end": "10", "knowledge_scope": {"valid_end": "1"}})
        with self.assertRaisesRegex(ValueError, "outside the current knowledge scope"):
            self.inspect(controls={"memory": {"scope_memory": {"pin": True}}})


if __name__ == "__main__":
    unittest.main()
