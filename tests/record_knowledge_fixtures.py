#!/usr/bin/env python3
"""Read-only card backlinks retain identity, source evidence and knowledge scope."""
from __future__ import annotations

import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_knowledge
import moyu_records
import server
from moyu_context import text_hash


WORK = "knowledge_work"
BRANCH = "knowledge_main"
STAMP = "2026-10-16T00:00:00+00:00"
BODY = "林岚看见🗝️铜钥匙，雨水落在窗边。"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class RecordKnowledge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="moyu-knowledge-")
        self.database = Path(self.tmp.name) / "synthetic.sqlite3"
        with patch.object(server, "DB_PATH", self.database):
            server.init_db()
        self.conn = sqlite3.connect(self.database)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("INSERT INTO project(id,schema_version,name,created_at) VALUES(?,?,?,?)",
                          (WORK, 11, "合成知识资料", STAMP))
        self.conn.execute("INSERT INTO branch(id,project_id,status) VALUES(?,?,'active')", (BRANCH, WORK))
        self.record("lan", "林岚", aliases=["阿岚"])
        self.record("key", "铜钥匙", kind="物品")
        self.record("other", "铁鹭")
        self.chapter("c1", 10)
        self.chapter("c2", 30)
        self.chapter("c3", 90)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def record(self, identifier, name, *, kind="人物", aliases=(), scope=None, description="合成资料"):
        self.conn.execute(
            "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status) VALUES(?,?,?,?,?,'active')",
            (identifier, WORK, kind, name, encoded(list(aliases))))
        self.conn.execute("INSERT INTO app_entity_meta VALUES(?,?,?)", (identifier, description, STAMP))
        moyu_records.write_scope(self.conn, identifier, scope or {}, STAMP)

    def chapter(self, identifier, ordinal, body=BODY, *, branch=BRANCH, kind="chapter", pov="阿岚"):
        self.conn.execute(
            "INSERT INTO document_node(id,project_id,branch_id,kind,title,ordinal) VALUES(?,?,?,?,?,?)",
            (identifier, WORK, branch, kind, "合成章 " + identifier, ordinal))
        self.revision(identifier, identifier + "_rev1", body, branch=branch)
        self.conn.execute("INSERT INTO app_chapter_meta(chapter_id,pov,updated_at) VALUES(?,?,?)",
                          (identifier, pov, STAMP))

    def revision(self, chapter, identifier, body=BODY, *, branch=BRANCH):
        self.conn.execute(
            "INSERT INTO document_revision(id,document_node_id,branch_id,content_hash,canonical_text,"
            "created_at,actor_id,operation) VALUES(?,?,?,?,?,?,?,?)",
            (identifier, chapter, branch, text_hash(body), body.encode("utf-8"), STAMP, "local-author", "fixture"))

    def span(self, identifier, chapter="c1", *, quote="🗝️铜钥匙", body=BODY, revision=None,
             start=None, status="valid"):
        left = body.index(quote) if start is None else start
        right = left + len(quote)
        self.conn.execute(
            "INSERT INTO source_span(id,project_id,revision_id,document_node_id,start_codepoint,end_codepoint,"
            "start_utf8,end_utf8,quote_hash,quote,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, WORK, revision or chapter + "_rev1", chapter, left, right,
             len(body[:left].encode("utf-8")), len(body[:right].encode("utf-8")), text_hash(quote), quote, status))
        return {"source_span_id": identifier, "source_chapter_id": chapter,
                "source_revision_id": revision or chapter + "_rev1",
                "source_quote": quote, "source_start": left, "source_end": right,
                "source_hash": text_hash(quote)}

    def relation(self, identifier="rel_a", source="lan", target="key", *, evidence=None, branch=BRANCH):
        self.conn.execute(
            "INSERT INTO app_story_relation(id,project_id,branch_id,from_entity_id,to_entity_id,relation_type,"
            "note,status,created_at,updated_at,evidence_json) VALUES(?,?,?,?,?,?,?,'active',?,?,?)",
            (identifier, WORK, branch, source, target, "持有", "人工关系备注", STAMP, STAMP, encoded(evidence or {})))

    def memory(self, identifier="mem_a", *, record="lan", evidence=None, status="accepted", payload=None):
        data = {"name": "发现铜钥匙", **(evidence or {}), **(payload or {})}
        self.conn.execute(
            "INSERT INTO candidate_memory(id,project_id,branch_id,entity_id,source_span_id,kind,payload_json,"
            "status,confidence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (identifier, WORK, BRANCH, record, (evidence or {}).get("source_span_id"), "fact",
             encoded(data), status, 1.0, STAMP))

    def assertion(self, identifier="assert_a", *, evidence=None, state="candidate", record="lan"):
        self.conn.execute(
            "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,predicate,object_json,"
            "epistemic_mode,state,confidence,evidence_json,reviewed_by,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, WORK, BRANCH, record, "发现", encoded({"name": "发现铜钥匙"}), "world_truth",
             "candidate", 1.0, encoded(evidence or []), "local-author", STAMP))
        if state != "candidate":
            self.conn.execute("UPDATE assertion SET state=? WHERE id=?", (state, identifier))

    def get(self, record="lan", chapter="c2", **kwargs):
        return moyu_knowledge.record_knowledge(self.conn, WORK, BRANCH, record, chapter, **kwargs)

    def item(self, result, group, identifier):
        return next(item for item in result[group] if item["id"] == identifier)

    def snapshot(self):
        return hashlib.sha256("\n".join(self.conn.iterdump()).encode("utf-8")).hexdigest()

    def test_stable_ids_and_directed_backlinks_survive_rename_and_same_names(self):
        self.record("same_name", "铜钥匙", kind="物品")
        self.relation()
        self.relation("rel_b", "other", "lan")
        self.conn.execute("UPDATE entity SET canonical_name='旧钥匙' WHERE id='key'")
        outgoing = self.get()
        self.assertEqual(outgoing["summary"], {"outgoing": 1, "incoming": 1, "sources": 0})
        self.assertEqual(outgoing["outgoing"][0]["other_record"]["id"], "key")
        self.assertEqual(outgoing["outgoing"][0]["other_record"]["name"], "旧钥匙")
        incoming = self.get("key")
        self.assertEqual(incoming["incoming"][0]["id"], "rel_a")
        self.assertEqual(incoming["incoming"][0]["other_record"]["id"], "lan")
        self.assertEqual(self.get("same_name")["summary"]["incoming"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_story_relation").fetchone()[0], 2)

    def test_author_relation_is_not_an_occurred_fact_or_model_material(self):
        self.relation()
        before = self.snapshot()
        result = self.get()
        relation = result["outgoing"][0]
        self.assertTrue(result["availability"]["allowed"])
        self.assertEqual(relation["evidence"]["status"], "unverified")
        self.assertEqual(relation["epistemic_mode"], "author_plan")
        self.assertFalse(relation["availability"]["allowed"])
        self.assertIn("author_plan", relation["availability"]["reasons"])
        self.assertTrue(relation["availability"]["reason_messages"])
        self.assertEqual(before, self.snapshot())

    def test_current_exact_unicode_source_has_codepoint_offsets(self):
        evidence = self.span("span_key")
        self.memory(evidence=evidence)
        self.relation(evidence={**evidence, "known_by": ["lan"]})
        result = self.get()
        relation = result["outgoing"][0]
        self.assertTrue(relation["availability"]["allowed"])
        self.assertEqual(relation["evidence"]["status"], "valid")
        source = result["sources"][0]
        self.assertTrue(source["availability"]["allowed"])
        self.assertEqual(source["source_chapter_title"], "合成章 c1")
        self.assertEqual(source["evidence"]["source_quote"], "🗝️铜钥匙")
        self.assertEqual(source["evidence"]["source_end"] - source["evidence"]["source_start"], 5)
        self.assertEqual(source["evidence"]["source_revision_id"], "c1_rev1")

    def test_future_secret_endpoint_is_visible_to_author_but_unavailable(self):
        moyu_records.write_scope(self.conn, "key", {
            "available_from": 3, "private": True, "known_by": ["other"]}, STAMP)
        evidence = self.span("span_key")
        self.relation(evidence=evidence)
        result = self.get()
        relation = result["outgoing"][0]
        self.assertEqual(relation["other_record"]["name"], "铜钥匙")
        self.assertFalse(relation["availability"]["allowed"])
        self.assertIn("future_reveal", relation["availability"]["reasons"])
        self.assertIn("pov_knowledge", relation["availability"]["reasons"])
        self.assertEqual(self.get("key")["availability"]["status"], "restricted")
        self.memory(evidence=evidence, record="key")
        self.assertFalse(self.get("key")["sources"][0]["availability"]["allowed"])

    def test_top_and_nested_chapter_boundaries_both_constrain_availability(self):
        cases = (
            ({"valid_start": "1", "knowledge_scope": {"valid_start": "3"}}, "future_state"),
            ({"valid_start": "3", "knowledge_scope": {"valid_start": "1"}}, "future_state"),
            ({"valid_end": "10", "knowledge_scope": {"valid_end": "1"}}, "expired_state"),
            ({"valid_end": "1", "knowledge_scope": {"valid_end": "10"}}, "expired_state"),
            ({"available_from": "3", "knowledge_scope": {"available_from": "1"}}, "future_reveal"),
            ({"available_from": "1", "knowledge_scope": {"available_from": "3"}}, "future_reveal"),
        )
        for declaration, reason in cases:
            with self.subTest(declaration=declaration):
                moyu_records.write_scope(self.conn, "key", declaration, STAMP)
                availability = self.get("key", chapter="c2")["availability"]
                self.assertFalse(availability["allowed"])
                self.assertEqual(availability["status_label"], "本章不可用")
                self.assertIn(reason, availability["reasons"])
        moyu_records.write_scope(self.conn, "key", {
            "valid_start": "1", "valid_end": "3", "available_from": "1",
            "knowledge_scope": {"valid_start": "2", "valid_end": "2", "available_from": "2"},
        }, STAMP)
        self.assertTrue(self.get("key", chapter="c2")["availability"]["allowed"])

    def test_plan_in_either_declaration_cannot_be_promoted_to_world_truth(self):
        for outer, inner in (("world_truth", "author_plan"), ("author_plan", "world_truth")):
            with self.subTest(outer=outer, inner=inner):
                moyu_records.write_scope(self.conn, "key", {
                    "epistemic_mode": outer, "knowledge_scope": {"epistemic_mode": inner},
                }, STAMP)
                availability = self.get("key")["availability"]
                self.assertFalse(availability["allowed"])
                self.assertIn("author_plan", availability["reasons"])
                self.assertTrue(availability["is_plan"])
                self.assertEqual(availability["epistemic_mode"], "author_plan")

    def test_boundary_intersection_preserves_identity_and_combined_pov_owners(self):
        moyu_records.write_scope(self.conn, "key", {
            "known_by": ["lan"], "epistemic_mode": "character_belief", "valid_start": "1",
            "knowledge_scope": {"known_by": ["lan"], "valid_start": "2"},
        }, STAMP)
        availability = self.get("key")["availability"]
        self.assertTrue(availability["allowed"], availability)
        for scope, reason in (
            ({"project_id": "another_work", "valid_start": "2"}, "project_scope"),
            ({"branch_id": "another_branch", "valid_start": "2"}, "branch_scope"),
            ({"known_by": ["other"], "valid_start": "2"}, "pov_knowledge"),
        ):
            with self.subTest(scope=scope):
                moyu_records.write_scope(self.conn, "key", {
                    "known_by": ["lan"], "private": True, "valid_start": "1",
                    "knowledge_scope": scope,
                }, STAMP)
                availability = self.get("key")["availability"]
                self.assertFalse(availability["allowed"])
                self.assertIn(reason, availability["reasons"])

    def test_source_and_relation_keep_all_primary_and_dependency_boundaries(self):
        evidence = self.span("boundary_evidence")
        self.memory("bounded_memory", record="key", evidence=evidence, payload={
            "valid_start": "1", "knowledge_scope": {"valid_start": "3"},
        })
        self.relation(evidence={**evidence, "valid_end": "10", "knowledge_scope": {"valid_end": "1"}})
        self.assertion(evidence=[{**evidence, "candidate_memory_id": "bounded_memory"}], state="canon")
        result = self.get()
        self.assertIn("expired_state", result["outgoing"][0]["availability"]["reasons"])
        assertion = self.item(result, "sources", "assert_a")
        self.assertIn("future_state", assertion["availability"]["reasons"])
        self.assertFalse(assertion["availability"]["allowed"])
        memory = self.item(self.get("key"), "sources", "bounded_memory")
        self.assertIn("future_state", memory["availability"]["reasons"])
        moyu_records.write_scope(self.conn, "key", {
            "available_from": "3", "knowledge_scope": {"available_from": "1"},
        }, STAMP)
        self.assertIn("future_reveal", self.get()["outgoing"][0]["availability"]["reasons"])
        self.assertIn("future_reveal", self.get("key")["incoming"][0]["availability"]["reasons"])

    def test_other_branch_edges_are_excluded_and_branch_limits_remain(self):
        self.conn.execute("INSERT INTO branch(id,project_id,status) VALUES('alternate',?,'active')", (WORK,))
        self.relation("other_branch", branch="alternate")
        self.relation("main_branch")
        moyu_records.write_scope(self.conn, "key", {"knowledge_scope": {"branches": ["alternate"]}}, STAMP)
        result = self.get()
        self.assertEqual([item["id"] for item in result["outgoing"]], ["main_branch"])
        self.assertIn("branch_scope", result["outgoing"][0]["availability"]["reasons"])
        with self.assertRaises(moyu_knowledge.KnowledgeNotFound):
            moyu_knowledge.record_knowledge(self.conn, WORK, "alternate", "lan", "c2")

    def test_no_selected_chapter_never_promises_material_can_be_used(self):
        result = self.get(chapter=None)
        self.assertEqual(result["availability"]["status"], "unselected")
        self.assertFalse(result["availability"]["allowed"])
        self.assertIn("chapter_not_selected", result["availability"]["reasons"])
        self.assertIsNone(result["scope"]["chapter_id"])

    def test_duplicate_pov_alias_does_not_grant_private_access(self):
        self.record("alias_twin", "另一林岚", aliases=["阿岚"])
        moyu_records.write_scope(self.conn, "key", {"known_by": ["lan"], "private": True}, STAMP)
        result = self.get("key")
        self.assertEqual(result["scope"]["pov_entity_ids"], [])
        self.assertEqual(result["scope"]["ambiguous_aliases"][0]["alias"], "阿岚")
        self.assertIn("pov_knowledge", result["availability"]["reasons"])

    def test_old_revision_and_deleted_source_stay_visible_as_stale(self):
        evidence = self.span("span_old")
        self.memory(evidence=evidence)
        self.relation(evidence=evidence)
        self.revision("c1", "c1_rev2", "正文已经改写。")
        result = self.get()
        self.assertEqual(result["sources"][0]["evidence"]["status"], "stale")
        self.assertIn("source_revision_outdated", result["sources"][0]["evidence"]["reasons"])
        self.assertEqual(result["outgoing"][0]["evidence"]["source_quote"], evidence["source_quote"])
        self.conn.execute("INSERT INTO app_deleted_node VALUES('c1',?,'合成删章')", (STAMP,))
        self.assertIn("missing_source_chapter", self.get()["sources"][0]["evidence"]["reasons"])

    def test_repeated_quote_requires_exact_current_range_and_hash(self):
        body = "钥匙落地。钥匙落地。"
        self.chapter("repeated", 1, body)
        evidence = self.span("span_second", "repeated", body=body, quote="钥匙", start=5)
        self.relation(evidence=evidence)
        self.assertEqual(self.get()["outgoing"][0]["evidence"]["status"], "valid")
        self.conn.execute("UPDATE app_story_relation SET evidence_json=? WHERE id='rel_a'",
                          (encoded({**evidence, "source_start": 0}),))
        result = self.get()["outgoing"][0]
        self.assertEqual(result["evidence"]["status"], "stale")
        self.assertIn("source_binding_mismatch", result["evidence"]["reasons"])
        self.assertFalse(result["availability"]["allowed"])

    def test_saved_utf8_offsets_must_match_the_actual_unicode_source(self):
        evidence = self.span("span_utf8")
        self.memory(evidence=evidence)
        self.relation(evidence=evidence)
        self.assertion(evidence=[{**evidence, "candidate_memory_id": "mem_a"}], state="canon")
        original = dict(self.conn.execute(
            "SELECT start_utf8,end_utf8 FROM source_span WHERE id='span_utf8'").fetchone())
        for field in ("start_utf8", "end_utf8"):
            with self.subTest(field=field):
                self.conn.execute("UPDATE source_span SET start_utf8=?,end_utf8=? WHERE id='span_utf8'",
                                  (original["start_utf8"], original["end_utf8"]))
                self.conn.execute(f"UPDATE source_span SET {field}=? WHERE id='span_utf8'",
                                  (original[field] + 1,))
                before = self.snapshot()
                result = self.get()
                items = [result["outgoing"][0], self.item(result, "sources", "mem_a"),
                         self.item(result, "sources", "assert_a")]
                for item in items:
                    with self.subTest(item=item["id"]):
                        self.assertEqual(item["evidence"]["status"], "stale")
                        self.assertIn("invalid_source_quote", item["evidence"]["reasons"])
                        self.assertFalse(item["availability"]["allowed"])
                self.assertEqual(before, self.snapshot())

    def test_declared_utf8_offsets_are_checked_as_bindings(self):
        evidence = self.span("span_utf8_declaration")
        start, end = evidence["source_start"], evidence["source_end"]
        declared = {**evidence, "source_start_utf8": str(len(BODY[:start].encode("utf-8"))),
                    "source_end_utf8": str(len(BODY[:end].encode("utf-8")))}
        self.relation(evidence=declared)
        self.assertEqual(self.get()["outgoing"][0]["evidence"]["status"], "valid")
        for field in ("source_start_utf8", "source_end_utf8"):
            with self.subTest(field=field):
                damaged = {**declared, field: int(declared[field]) + 1}
                self.conn.execute("UPDATE app_story_relation SET evidence_json=? WHERE id='rel_a'",
                                  (encoded(damaged),))
                relation = self.get()["outgoing"][0]
                self.assertEqual(relation["evidence"]["status"], "stale")
                self.assertIn("source_binding_mismatch", relation["evidence"]["reasons"])
                self.assertFalse(relation["availability"]["allowed"])

    def test_each_primary_and_alias_range_must_agree_with_the_saved_span(self):
        evidence = self.span("span_aliases")
        declared = {**evidence, "source_start_codepoint": str(evidence["source_start"]),
                    "source_end_codepoint": str(evidence["source_end"])}
        self.relation(evidence=declared)
        self.assertEqual(self.get()["outgoing"][0]["evidence"]["status"], "valid")
        for field in ("source_start", "source_start_codepoint", "source_end", "source_end_codepoint"):
            with self.subTest(field=field):
                damaged = {**declared, field: int(declared[field]) + 1}
                self.conn.execute("UPDATE app_story_relation SET evidence_json=? WHERE id='rel_a'",
                                  (encoded(damaged),))
                relation = self.get()["outgoing"][0]
                self.assertEqual(relation["evidence"]["status"], "stale")
                self.assertIn("source_binding_mismatch", relation["evidence"]["reasons"])
                self.assertFalse(relation["availability"]["allowed"])

    def test_repeated_unicode_source_keeps_its_exact_range_with_numeric_strings(self):
        quote = "🗝️铜钥匙"
        body = quote + "落地。门缝透光。" + quote + "落地。"
        second = body.index(quote, len(quote))
        self.chapter("repeated_unicode", 1, body)
        evidence = self.span("span_repeated_unicode", "repeated_unicode", body=body,
                             quote=quote, start=second)
        declared = {**evidence, "source_start": str(second),
                    "source_end": str(second + len(quote)),
                    "source_start_codepoint": str(second),
                    "source_end_codepoint": str(second + len(quote)),
                    "source_start_utf8": str(len(body[:second].encode("utf-8"))),
                    "source_end_utf8": str(len(body[:second + len(quote)].encode("utf-8")))}
        self.relation(evidence=declared)
        relation = self.get()["outgoing"][0]
        self.assertEqual(relation["evidence"]["status"], "valid")
        self.assertEqual(relation["evidence"]["source_start"], second)
        self.assertTrue(relation["availability"]["allowed"])
        rebound = {**declared, "source_start": "0", "source_end": str(len(quote)),
                   "source_start_codepoint": "0", "source_end_codepoint": str(len(quote)),
                   "source_start_utf8": "0", "source_end_utf8": str(len(quote.encode("utf-8")))}
        self.conn.execute("UPDATE app_story_relation SET evidence_json=? WHERE id='rel_a'",
                          (encoded(rebound),))
        relation = self.get()["outgoing"][0]
        self.assertEqual(relation["evidence"]["status"], "stale")
        self.assertIn("source_binding_mismatch", relation["evidence"]["reasons"])
        self.assertFalse(relation["availability"]["allowed"])

    def test_bool_and_float_offsets_cannot_equal_integer_bindings(self):
        self.chapter("small_range", 1, "钥门")
        evidence = self.span("span_small_range", "small_range", body="钥门", quote="钥")
        declared = {**evidence, "source_start_codepoint": 0, "source_end_codepoint": 1,
                    "source_start_utf8": 0, "source_end_utf8": 3}
        self.relation(evidence=declared)
        self.assertEqual(self.get()["outgoing"][0]["evidence"]["status"], "valid")
        fields = ("source_start", "source_start_codepoint", "source_end", "source_end_codepoint",
                  "source_start_utf8", "source_end_utf8")
        for field in fields:
            for value in (bool(declared[field]), float(declared[field])):
                with self.subTest(field=field, value=value):
                    damaged = {**declared, field: value}
                    self.conn.execute("UPDATE app_story_relation SET evidence_json=? WHERE id='rel_a'",
                                      (encoded(damaged),))
                    relation = self.get()["outgoing"][0]
                    self.assertEqual(relation["evidence"]["status"], "stale")
                    self.assertIn("source_binding_mismatch", relation["evidence"]["reasons"])
                    self.assertFalse(relation["availability"]["allowed"])

    def test_chapter_hash_still_guards_relationship_and_memory_sources(self):
        evidence = {**self.span("span_chapter_hash"), "chapter_hash": text_hash(BODY)}
        self.memory(evidence=evidence)
        self.relation(evidence=evidence)
        self.assertion(evidence=[{**evidence, "candidate_memory_id": "mem_a"}], state="canon")
        initial = self.get()
        self.assertTrue(all(item["evidence"]["status"] == "valid"
                            for item in [*initial["outgoing"], *initial["sources"]]))
        damaged = {**evidence, "chapter_hash": text_hash("已经变动的整章正文")}
        self.conn.execute("UPDATE app_story_relation SET evidence_json=? WHERE id='rel_a'",
                          (encoded(damaged),))
        self.conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id='mem_a'",
                          (encoded({"name": "发现铜钥匙", **damaged}),))
        self.conn.execute("UPDATE assertion SET evidence_json=? WHERE id='assert_a'",
                          (encoded([{**damaged, "candidate_memory_id": "mem_a"}]),))
        result = self.get()
        for item in [*result["outgoing"], *result["sources"]]:
            with self.subTest(item=item["id"]):
                self.assertEqual(item["evidence"]["status"], "stale")
                self.assertIn("invalid_source_quote", item["evidence"]["reasons"])
                self.assertFalse(item["availability"]["allowed"])

    def test_memory_payload_span_id_cannot_be_hidden_by_the_database_binding(self):
        evidence = self.span("span_bound")
        self.span("span_same_quote")
        self.memory(evidence=evidence, payload={"source_span_id": "span_same_quote"})
        self.assertion(evidence=[{**evidence, "candidate_memory_id": "mem_a"}], state="canon")
        before = self.snapshot()
        result = self.get()
        for identifier in ("mem_a", "assert_a"):
            with self.subTest(identifier=identifier):
                item = self.item(result, "sources", identifier)
                self.assertEqual(item["evidence"]["source_span_id"], "span_bound")
                self.assertEqual(item["evidence"]["status"], "stale")
                self.assertIn("source_binding_mismatch", item["evidence"]["reasons"])
                self.assertFalse(item["availability"]["allowed"])
        self.assertEqual(before, self.snapshot())

    def test_unconfirmed_and_sourceless_memories_are_not_promoted(self):
        evidence = self.span("span_candidate")
        self.memory("candidate", evidence=evidence, status="candidate")
        self.memory("no_source", status="accepted")
        result = self.get()
        candidate = self.item(result, "sources", "candidate")
        missing = self.item(result, "sources", "no_source")
        self.assertEqual(candidate["evidence"]["status"], "valid")
        self.assertIn("unconfirmed_memory", candidate["availability"]["reasons"])
        self.assertEqual(missing["evidence"]["status"], "unverified")
        self.assertFalse(missing["availability"]["allowed"])

    def test_assertion_uses_latest_real_source_and_preserves_legacy_notes(self):
        early, later = self.span("early"), self.span("later", "c3")
        self.memory("early_memory", evidence=early)
        self.memory("later_memory", evidence=later)
        self.assertion(evidence=[
            "旧资料备注", 8, None, ["只是备注"],
            {**early, "candidate_memory_id": "early_memory"},
            {**later, "candidate_memory_id": "later_memory"},
        ], state="canon")
        before = self.snapshot()
        source = self.item(self.get(), "sources", "assert_a")
        self.assertEqual(source["evidence"]["status"], "valid")
        self.assertEqual(source["evidence"]["source_chapter_id"], "c3")
        self.assertEqual(len(source["evidence"]["entries"]), 2)
        self.assertEqual(source["evidence"]["entries_total"], 6)
        self.assertIn("future_source", source["availability"]["reasons"])
        self.assertFalse(source["availability"]["allowed"])
        self.assertEqual(before, self.snapshot())

    def test_withdrawn_memory_or_missing_span_does_not_hide_source_failure(self):
        evidence = self.span("span_withdrawn")
        self.memory(evidence=evidence)
        self.assertion(evidence=[{**evidence, "candidate_memory_id": "mem_a"}], state="canon")
        self.conn.execute("UPDATE candidate_memory SET status='rejected' WHERE id='mem_a'")
        source = self.item(self.get(), "sources", "assert_a")
        self.assertEqual(source["evidence"]["status"], "stale")
        self.assertIn("source_memory_unconfirmed", source["availability"]["reasons"])
        self.conn.execute("UPDATE app_entity_scope SET scope_json='{}' WHERE entity_id='lan'")
        self.relation(evidence={"source_span_id": "missing_span"})
        source = self.get()["outgoing"][0]["evidence"]
        self.assertEqual(source["status"], "stale")
        self.assertEqual(source["source_span_id"], "missing_span")

    def test_assertion_dependencies_keep_other_entity_secrets_and_memory_binding(self):
        evidence = self.span("span_private")
        self.memory("private_memory", record="key", evidence=evidence)
        self.assertion(evidence=[{**evidence, "candidate_memory_id": "private_memory"}], state="canon")
        moyu_records.write_scope(self.conn, "key", {"known_by": ["other"], "private": True}, STAMP)
        source = self.item(self.get(), "sources", "assert_a")
        self.assertEqual(source["evidence"]["status"], "valid")
        self.assertIn("pov_knowledge", source["availability"]["reasons"])
        self.assertFalse(source["availability"]["allowed"])
        self.conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id='private_memory'",
                          (encoded({"name": "伪造旧来源", **evidence, "source_quote": "已经变化的原句"}),))
        source = self.item(self.get(), "sources", "assert_a")
        self.assertEqual(source["evidence"]["status"], "stale")
        self.assertIn("source_binding_mismatch", source["availability"]["reasons"])

    def test_damaged_json_and_scope_are_reported_instead_of_empty_sources(self):
        evidence = self.span("span_damaged")
        self.memory(evidence=evidence)
        self.conn.execute("UPDATE candidate_memory SET payload_json='not-json' WHERE id='mem_a'")
        self.relation()
        self.conn.execute("UPDATE app_story_relation SET evidence_json='not-json' WHERE id='rel_a'")
        result = self.get()
        self.assertEqual(result["summary"]["sources"], 1)
        self.assertEqual(result["sources"][0]["evidence"]["status"], "error")
        self.assertEqual(result["outgoing"][0]["evidence"]["status"], "error")
        self.assertEqual(result["errors"][0]["id"], "mem_a")
        moyu_records.write_scope(self.conn, "key", {}, STAMP)
        self.conn.execute("UPDATE app_entity_scope SET scope_json='[]' WHERE entity_id='key'")
        other = self.get()["outgoing"][0]
        self.assertIn("record_error", other["availability"]["reasons"])
        self.assertNotIn("missing_record", other["availability"]["reasons"])
        self.assertEqual(other["other_record"]["name"], "铜钥匙")
        self.conn.execute("UPDATE app_entity_scope SET scope_json='[]' WHERE entity_id='lan'")
        with self.assertRaisesRegex(ValueError, "设定可见范围"):
            self.get()

    def test_archived_identity_does_not_reconnect_to_new_card_with_same_name(self):
        self.relation()
        self.conn.execute("UPDATE entity SET status='archived' WHERE id='key'")
        self.record("replacement_key", "铜钥匙", kind="物品")
        row = self.get()["outgoing"][0]
        self.assertEqual(row["other_record"]["id"], "key")
        self.assertIn("archived_record", row["availability"]["reasons"])
        self.assertEqual(self.get("replacement_key")["summary"]["incoming"], 0)
        with self.assertRaises(moyu_knowledge.KnowledgeNotFound):
            self.get("key")

    def test_active_chapter_number_ignores_ordinal_gaps_and_deleted_chapters(self):
        moyu_records.write_scope(self.conn, "lan", {"available_from": 3}, STAMP)
        self.assertTrue(self.get(chapter="c3")["availability"]["allowed"])
        self.assertEqual(self.get(chapter="c3")["scope"]["chapter_number"], 3)
        self.conn.execute("INSERT INTO app_deleted_node VALUES('c1',?,'合成删章')", (STAMP,))
        result = self.get(chapter="c3")
        self.assertEqual(result["scope"]["chapter_number"], 2)
        self.assertIn("future_reveal", result["availability"]["reasons"])
        with self.assertRaises(moyu_knowledge.KnowledgeNotFound):
            self.get(chapter="c1")

    def test_bounded_pagination_preserves_totals_and_stable_order(self):
        for index in range(1007):
            self.relation(f"rel_{index:04d}")
        for index in range(17):
            self.memory(f"mem_{index:04d}", status="candidate")
        first, second = self.get(limit=7), self.get(limit=7, offset=7)
        self.assertEqual(first["summary"], {"outgoing": 1007, "incoming": 0, "sources": 17})
        self.assertEqual(len(first["outgoing"]), 7)
        self.assertEqual(len(first["sources"]), 7)
        self.assertEqual(first["pagination"]["outgoing"]["next_offset"], 7)
        self.assertEqual(first["pagination"]["incoming"]["has_more"], False)
        self.assertFalse(set(row["id"] for row in first["outgoing"]) &
                         set(row["id"] for row in second["outgoing"]))
        last = self.get(limit=7, offset=1001)
        self.assertEqual(len(last["outgoing"]), 6)
        self.assertFalse(last["pagination"]["outgoing"]["has_more"])
        self.assertIsNone(last["pagination"]["outgoing"]["next_offset"])
        self.assertEqual(self.get(limit=7, offset=2000)["outgoing"], [])
        for options in ({"limit": 0}, {"limit": True}, {"limit": 101}, {"offset": -1},
                        {"offset": False}, {"offset": 1_000_001}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.get(**options)

    def test_scope_not_found_invalid_ids_and_database_errors_are_distinct(self):
        for work, branch, record, chapter in (
            ("absent", BRANCH, "lan", "c2"), (WORK, "absent", "lan", "c2"),
            (WORK, BRANCH, "absent", "c2"), (WORK, BRANCH, "lan", "absent"),
        ):
            with self.subTest(work=work, branch=branch, record=record, chapter=chapter):
                with self.assertRaises(moyu_knowledge.KnowledgeNotFound):
                    moyu_knowledge.record_knowledge(self.conn, work, branch, record, chapter)
        for identifier in ("../lan", "", "a" * 129, None):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.get(record=identifier)
        self.conn.execute("ALTER TABLE app_story_relation RENAME TO hidden_relationships")
        with self.assertRaises(sqlite3.OperationalError):
            self.get()

    def test_actual_http_route_paginates_and_distinguishes_validation_failures(self):
        self.relation("http_a", evidence=self.span("http_span"))
        self.relation("http_b")
        with server.workspace.use_scope(*server.workspace.resolve_scope(self.conn, WORK, BRANCH)):
            server.ensure_seed(self.conn)
        self.conn.commit()
        before = self.snapshot()
        with patch.object(server, "DB_PATH", self.database), \
                patch.object(server.Handler, "log_message", lambda *_args: None):
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()

            def call(path):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{http.server_port}" + path,
                    headers={"X-Moyu-Project": WORK, "X-Moyu-Branch": BRANCH})
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, json.load(error)

            try:
                status, page = call("/api/records/lan/knowledge?chapter_id=c2&limit=1")
                self.assertEqual(status, 200, page)
                self.assertEqual(page["summary"]["outgoing"], 2)
                self.assertEqual(page["outgoing"][0]["id"], "http_a")
                self.assertEqual(page["pagination"]["outgoing"]["next_offset"], 1)
                self.assertEqual(call("/api/records/lan/knowledge?chapter_id=c2&limit=0")[0], 400)
                self.assertEqual(call("/api/records/lan/knowledge?limit=1&limit=2")[0], 400)
                self.assertEqual(call("/api/records/missing/knowledge?chapter_id=c2")[0], 404)
                self.assertEqual(call("/api/records/lan/knowledge?chapter_id=missing")[0], 404)
                self.assertEqual(before, self.snapshot())
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_author_read_works_in_query_only_mode_without_writes_or_commits(self):
        self.relation(evidence=self.span("readonly_span"))
        self.conn.commit()
        before, changes = self.snapshot(), self.conn.total_changes
        self.conn.execute("PRAGMA query_only=ON")
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(len(self.get()["outgoing"]), 1)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.total_changes, changes)
        self.assertEqual(before, self.snapshot())
        with self.assertRaises(moyu_knowledge.KnowledgeNotFound):
            self.get(record="not_here")
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("PRAGMA query_only=OFF")
        self.conn.execute("UPDATE entity SET canonical_name='未提交的新名字' WHERE id='lan'")
        self.assertTrue(self.conn.in_transaction)
        self.get()
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.get()["record"]["name"], "林岚")

    def test_count_and_page_share_one_snapshot_during_concurrent_change(self):
        self.relation("before")
        self.conn.commit()
        self.conn.execute("PRAGMA journal_mode=WAL")
        writer = sqlite3.connect(self.database)
        writer.execute("PRAGMA foreign_keys=ON")
        original = moyu_knowledge._one
        inserted = False

        def read_then_write(conn, sql, parameters=()):
            nonlocal inserted
            row = original(conn, sql, parameters)
            if not inserted and "COUNT(*) total FROM app_story_relation" in sql:
                inserted = True
                writer.execute(
                    "INSERT INTO app_story_relation(id,project_id,branch_id,from_entity_id,to_entity_id,"
                    "relation_type,note,status,created_at,updated_at,evidence_json)"
                    " VALUES('after',?,?, 'lan','key','新增','', 'active',?,?,'{}')",
                    (WORK, BRANCH, STAMP, STAMP))
                writer.commit()
            return row

        try:
            with patch.object(moyu_knowledge, "_one", side_effect=read_then_write):
                result = self.get()
            self.assertTrue(inserted)
            self.assertEqual(result["summary"]["outgoing"], 1)
            self.assertEqual([row["id"] for row in result["outgoing"]], ["before"])
            self.assertEqual(self.get()["summary"]["outgoing"], 2)
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
