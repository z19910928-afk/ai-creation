#!/usr/bin/env python3
"""Real SQLite author decisions, late-result rejection and evidence invalidation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moyu_analysis import (  # noqa: E402
    AnalysisConflict, decide_analysis, decide_review, list_analysis, list_reviews,
    review_repair_source, save_analysis, save_review, invalidate_chapter,
)
from moyu_context import text_hash  # noqa: E402


class AnalysisWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE document_node(id TEXT PRIMARY KEY,project_id TEXT,branch_id TEXT,kind TEXT,title TEXT,ordinal INTEGER);
            CREATE TABLE document_revision(id TEXT PRIMARY KEY,document_node_id TEXT,branch_id TEXT,canonical_text BLOB);
            CREATE TABLE app_deleted_node(chapter_id TEXT PRIMARY KEY);
            CREATE TABLE app_chapter_meta(chapter_id TEXT PRIMARY KEY,recap TEXT,next_brief TEXT,updated_at TEXT);
            CREATE TABLE canon_artifact(id TEXT PRIMARY KEY,payload_json TEXT);
        """)
        self.scope = {
            "project_id": "book", "branch_id": "main", "actor_id": "author",
            "records": [{"id": "lan", "name": "林岚"}, {"id": "keeper", "name": "守灯人"}],
        }
        self.body = "林岚把钥匙交给守灯人。\n守灯人答应在子夜回来。\n她的左肩仍然疼痛。"
        self.chapter = {"id": "chapter", "body": self.body, "source_revision_id": "r1"}
        self.conn.execute("INSERT INTO document_node VALUES(?,?,?,?,?,?)", ("chapter", "book", "main", "chapter", "夜航", 1))
        self.conn.execute("INSERT INTO document_revision VALUES(?,?,?,?)", ("r1", "chapter", "main", self.body.encode()))
        self.conn.execute("INSERT INTO app_chapter_meta VALUES(?,?,?,?)", ("chapter", "", "", ""))
        self.raw = {
            "summary": {"text": "林岚交出钥匙。", "evidence": [{"source_quote": "林岚把钥匙交给守灯人。"}]},
            "handoff": {"text": "等子夜守灯人回来。", "evidence": [{"source_quote": "守灯人答应在子夜回来。"}]},
            "candidates": [
                {"kind": "event", "name": "交出钥匙", "note": "林岚把钥匙交给守灯人。", "source_quote": "林岚把钥匙交给守灯人。", "epistemic_mode": "world_truth", "known_by": ["林岚", "守灯人"]},
                {"kind": "character_state", "name": "肩伤", "note": "左肩仍有疼痛。", "subject_name": "林岚", "attribute": "左肩", "value": "疼痛", "source_quote": "她的左肩仍然疼痛。", "epistemic_mode": "world_truth", "known_by": ["lan"]},
            ],
        }
        self.apply_count = 0
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def apply(self, conn, scope, candidate):
        self.apply_count += 1
        conn.execute("INSERT INTO canon_artifact VALUES(?,?)", (candidate["id"], json.dumps(candidate, ensure_ascii=False)))
        return {"memory_id": candidate["id"]}

    def payload(self, analysis, decisions=None, **extra):
        return {
            "source_revision_id": analysis["source_revision_id"], "chapter_hash": analysis["chapter_hash"],
            "decisions": decisions or [], **extra,
        }

    def revise(self, body, revision="r2"):
        self.conn.execute("INSERT INTO document_revision VALUES(?,?,?,?)", (revision, "chapter", "main", body.encode()))

    def test_saved_normal_prose_and_confirmed_handoff(self):
        stored = save_analysis(self.conn, self.scope, self.chapter, "run1", self.raw)
        self.assertEqual(stored["summary"]["status"], "pending")
        self.assertEqual(stored["candidates"][0]["known_by"], ["keeper", "lan"])
        self.assertEqual(self.conn.execute("SELECT recap FROM app_chapter_meta").fetchone()[0], "")
        decided = decide_analysis(
            self.conn, self.scope, stored["id"],
            self.payload(stored, [{"candidate_id": stored["candidates"][0]["id"], "decision": "accepted"}, {"candidate_id": stored["candidates"][1]["id"], "decision": "rejected"}], confirm_summary=True, confirm_handoff=True),
            {"apply_candidate": self.apply},
        )
        self.assertEqual(decided["status"], "reviewed")
        self.assertEqual(self.apply_count, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canon_artifact").fetchone()[0], 1)
        self.assertEqual(tuple(self.conn.execute("SELECT recap,next_brief FROM app_chapter_meta").fetchone()), ("林岚交出钥匙。", "等子夜守灯人回来。"))
        self.assertEqual(list_analysis(self.conn, self.scope)[0]["handoff"]["status"], "accepted")

    def test_same_revision_generation_and_decision_retry_are_idempotent(self):
        first = save_analysis(self.conn, self.scope, self.chapter, "run1", self.raw)
        repeated = save_analysis(self.conn, self.scope, self.chapter, "run2", self.raw)
        self.assertEqual(first["id"], repeated["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_chapter_analysis").fetchone()[0], 1)
        data = self.payload(first, [{"candidate_id": first["candidates"][0]["id"], "decision": "accepted"}])
        decide_analysis(self.conn, self.scope, first["id"], data, {"apply_candidate": self.apply})
        retry = decide_analysis(self.conn, self.scope, first["id"], data, {"apply_candidate": self.apply})
        self.assertEqual(retry["confirmed_candidates"], [])
        self.assertEqual(self.apply_count, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canon_artifact").fetchone()[0], 1)
        changed = self.payload(first, [{"candidate_id": first["candidates"][0]["id"], "decision": "rejected"}])
        with self.assertRaises(AnalysisConflict):
            decide_analysis(self.conn, self.scope, first["id"], changed, {"apply_candidate": self.apply})

    def test_mixed_callback_failure_rolls_back_all_candidates(self):
        first = save_analysis(self.conn, self.scope, self.chapter, "run1", self.raw)
        def fail_second(conn, scope, candidate):
            self.apply(conn, scope, candidate)
            if candidate["kind"] == "character_state":
                raise RuntimeError("simulated persistence failure")
            return {"memory_id": candidate["id"]}
        decisions = [{"candidate_id": candidate["id"], "decision": "accepted"} for candidate in first["candidates"]]
        with self.assertRaisesRegex(RuntimeError, "persistence"):
            decide_analysis(self.conn, self.scope, first["id"], self.payload(first, decisions, confirm_summary=True), {"apply_candidate": fail_second})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canon_artifact").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT recap FROM app_chapter_meta").fetchone()[0], "")
        fresh = list_analysis(self.conn, self.scope)[0]
        self.assertEqual(fresh["decisions"], [])
        self.assertTrue(all(candidate["status"] == "candidate" for candidate in fresh["candidates"]))
        self.assertEqual(fresh["summary"]["status"], "pending")

    def test_caller_rollback_remains_in_control_after_decision(self):
        first = save_analysis(self.conn, self.scope, self.chapter, "run1", self.raw)
        self.conn.commit()
        decide_analysis(
            self.conn, self.scope, first["id"],
            self.payload(first, [{"candidate_id": first["candidates"][0]["id"], "decision": "accepted"}], confirm_summary=True),
            {"apply_candidate": self.apply},
        )
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canon_artifact").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT recap FROM app_chapter_meta").fetchone()[0], "")
        self.assertEqual(list_analysis(self.conn, self.scope)[0]["decisions"], [])

    def test_edit_invalidates_automatic_summary_but_preserves_author_changes(self):
        first = save_analysis(self.conn, self.scope, self.chapter, "run1", self.raw)
        decide_analysis(self.conn, self.scope, first["id"], self.payload(first, confirm_summary=True, confirm_handoff=True))
        self.conn.execute("UPDATE app_chapter_meta SET next_brief='作者自行写的下一章要求' WHERE chapter_id='chapter'")
        changed = "林岚保留了钥匙，独自离开渡口。"
        self.revise(changed)
        result = invalidate_chapter(self.conn, self.scope, "chapter", "r2", text_hash(changed))
        self.assertEqual(result["analyses"], [first["id"]])
        self.assertEqual(tuple(self.conn.execute("SELECT recap,next_brief FROM app_chapter_meta").fetchone()), ("", "作者自行写的下一章要求"))
        self.assertEqual(list_analysis(self.conn, self.scope)[0]["status"], "stale")

    def test_late_analysis_and_old_confirmation_cannot_change_new_manuscript(self):
        first = save_analysis(self.conn, self.scope, self.chapter, "run1", self.raw)
        self.revise("林岚保留了钥匙，独自离开渡口。")
        with self.assertRaises(AnalysisConflict):
            save_analysis(self.conn, self.scope, self.chapter, "late-run", self.raw)
        with self.assertRaises(AnalysisConflict):
            decide_analysis(self.conn, self.scope, first["id"], self.payload(first, [{"candidate_id": first["candidates"][0]["id"], "decision": "accepted"}], confirm_handoff=True), {"apply_candidate": self.apply})
        self.assertEqual(self.apply_count, 0)
        self.assertEqual(self.conn.execute("SELECT next_brief FROM app_chapter_meta").fetchone()[0], "")
        stale = list_analysis(self.conn, self.scope)[0]
        self.assertEqual(stale["status"], "stale")
        self.assertTrue(stale["handoff"]["stale"])

    def test_scope_and_deleted_source_are_checked(self):
        stored = save_analysis(self.conn, self.scope, self.chapter, "run1", self.raw)
        foreign = {**self.scope, "project_id": "other-book"}
        self.assertEqual(list_analysis(self.conn, foreign), [])
        with self.assertRaises(ValueError):
            decide_analysis(self.conn, foreign, stored["id"], self.payload(stored))
        self.conn.execute("INSERT INTO app_deleted_node VALUES(?)", ("chapter",))
        with self.assertRaises(AnalysisConflict):
            decide_analysis(self.conn, self.scope, stored["id"], self.payload(stored, confirm_handoff=True))

    def test_edited_candidate_cannot_replace_its_original_quote(self):
        stored = save_analysis(self.conn, self.scope, self.chapter, "run1", self.raw)
        candidate = stored["candidates"][0]
        data = self.payload(stored, [{"candidate_id": candidate["id"], "decision": "accepted", "edited": {"source_quote": "另一句"}}])
        with self.assertRaisesRegex(ValueError, "来源"):
            decide_analysis(self.conn, self.scope, stored["id"], data, {"apply_candidate": self.apply})
        data["decisions"][0]["edited"] = {"note": "作者确认她把钥匙交了出去。"}
        result = decide_analysis(self.conn, self.scope, stored["id"], data, {"apply_candidate": self.apply})
        self.assertEqual(result["candidates"][0]["source_quote"], candidate["source_quote"])
        self.assertEqual(result["candidates"][0]["note"], "作者确认她把钥匙交了出去。")
        self.assertEqual(result["candidates"][0]["source_revision_id"], "r1")

    def test_unresolved_character_knowledge_requires_resolution_before_confirmation(self):
        raw = copy.deepcopy(self.raw)
        raw["candidates"][0]["known_by"] = ["陌生人"]
        stored = save_analysis(self.conn, self.scope, self.chapter, "run1", raw)
        candidate = stored["candidates"][0]
        self.assertEqual(candidate["known_by_unresolved"], ["陌生人"])
        data = self.payload(stored, [{"candidate_id": candidate["id"], "decision": "accepted"}])
        with self.assertRaisesRegex(ValueError, "知情"):
            decide_analysis(self.conn, self.scope, stored["id"], data, {"apply_candidate": self.apply})
        data["decisions"][0]["edited"] = {"known_by": ["lan"]}
        confirmed = decide_analysis(self.conn, self.scope, stored["id"], data, {"apply_candidate": self.apply})
        self.assertEqual(confirmed["candidates"][0]["known_by"], ["lan"])
        self.assertNotIn("known_by_unresolved", confirmed["candidates"][0])

    def test_unresolved_character_candidate_can_be_rejected_without_canon_write(self):
        raw = copy.deepcopy(self.raw)
        raw["candidates"][0]["known_by"] = ["陌生人"]
        stored = save_analysis(self.conn, self.scope, self.chapter, "run1", raw)
        candidate = stored["candidates"][0]
        data = self.payload(stored, [{"candidate_id": candidate["id"], "decision": "rejected"}])
        rejected = decide_analysis(self.conn, self.scope, stored["id"], data, {"apply_candidate": self.apply})
        self.assertEqual(rejected["candidates"][0]["status"], "rejected")
        self.assertEqual(rejected["candidates"][0]["known_by_unresolved"], ["陌生人"])
        self.assertEqual(rejected["candidates"][0]["source_quote"], candidate["source_quote"])
        self.assertEqual(rejected["confirmed_candidates"], [])
        self.assertEqual(self.apply_count, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canon_artifact").fetchone()[0], 0)
        repeated = decide_analysis(self.conn, self.scope, stored["id"], data, {"apply_candidate": self.apply})
        self.assertEqual(len(repeated["decisions"]), 1)
        self.revise("作者已经改过本章。")
        with self.assertRaises(AnalysisConflict):
            decide_analysis(self.conn, self.scope, stored["id"], data, {"apply_candidate": self.apply})

    def test_evidence_review_decisions_and_stale_prior_chapter(self):
        old = "林岚把唯一的钥匙交给了守灯人。"
        self.conn.execute("INSERT INTO document_node VALUES(?,?,?,?,?,?)", ("past", "book", "main", "chapter", "旧章", 0))
        self.conn.execute("INSERT INTO document_revision VALUES(?,?,?,?)", ("past-r1", "past", "main", old.encode()))
        raw = {
            "findings": [{"category": "continuity", "severity": "possible", "message": "钥匙去向要在后文交代。", "evidence": [
                {"source_chapter_id": "past", "source_quote": old},
                {"source_chapter_id": "chapter", "source_quote": "林岚把钥匙交给守灯人。"},
            ]}],
            "uncertainties": ["未检查后续正文。"],
        }
        report = save_review(self.conn, self.scope, self.chapter, "review-run", raw)
        issue_id = report["findings"][0]["id"]
        with self.assertRaisesRegex(ValueError, "理由"):
            decide_review(self.conn, self.scope, report["id"], issue_id, {"decision": "ignored"})
        handled = decide_review(self.conn, self.scope, report["id"], issue_id, {"decision": "explained", "reason": "下一章会写归还钥匙的过程。"})
        self.assertEqual(handled["status"], "resolved_by_author")
        self.assertFalse(handled["auto_pass"])
        self.assertFalse(handled["literary_quality_verified"])
        duplicate = decide_review(self.conn, self.scope, report["id"], issue_id, {"decision": "explained", "reason": "下一章会写归还钥匙的过程。"})
        self.assertEqual(len(duplicate["decision_log"]), 1)
        self.conn.execute("INSERT INTO document_revision VALUES(?,?,?,?)", ("past-r2", "past", "main", "旧章已改。".encode()))
        self.assertEqual(list_reviews(self.conn, self.scope)[0]["status"], "stale")
        with self.assertRaises(AnalysisConflict):
            decide_review(self.conn, self.scope, report["id"], issue_id, {"decision": "explained", "reason": "下一章会写归还钥匙的过程。"})

    def test_accepting_review_does_not_claim_fix_and_empty_report_is_unverified(self):
        raw = {"findings": [{"category": "style", "severity": "suggestion", "message": "可以让动作更具体。", "evidence": [{"source_quote": "她的左肩仍然疼痛。"}]}], "uncertainties": []}
        report = save_review(self.conn, self.scope, self.chapter, "review-run", raw)
        handled = decide_review(self.conn, self.scope, report["id"], report["findings"][0]["id"], {"decision": "accepted", "reason": ""})
        self.assertEqual(handled["status"], "needs_revision")
        self.assertEqual(handled["findings"][0]["status"], "needs_revision")
        self.assertEqual(self.conn.execute("SELECT canonical_text FROM document_revision WHERE id='r1'").fetchone()[0], self.body.encode())
        empty = save_review(self.conn, self.scope, self.chapter, "empty", {"findings": [], "uncertainties": []})
        self.assertEqual(empty["status"], "no_findings_unverified")
        self.assertFalse(empty["auto_pass"])
        self.assertEqual(empty["chapter_hash"], text_hash(self.body))

    def test_model_review_can_only_quote_actual_sent_passage(self):
        first_quote = "林岚把钥匙交给守灯人。"
        scope = {
            **self.scope, "sent_context": {
                "current_chapter": {
                    "id": "chapter", "body": first_quote, "source_revision_id": "r1",
                    "source_range": {"start": 0, "end": len(first_quote)},
                },
            },
        }
        raw = {
            "findings": [{
                "category": "style", "severity": "suggestion", "message": "交钥匙时可以有具体动作。",
                "evidence": [{"source_quote": first_quote}],
            }], "uncertainties": [],
        }
        report = save_review(self.conn, scope, self.chapter, "review", raw)
        self.assertTrue(report["evidence_scope_verified"])
        forbidden = copy.deepcopy(raw)
        forbidden["findings"][0]["evidence"] = [{"source_quote": "守灯人答应在子夜回来。"}]
        with self.assertRaisesRegex(ValueError, "sent to the model"):
            save_review(self.conn, scope, self.chapter, "unseen", forbidden)
        self.assertEqual(len(list_reviews(self.conn, scope)), 1)

    def test_review_repair_source_returns_revision_bound_replace_range(self):
        raw = {
            "findings": [{
                "category": "style", "severity": "suggestion",
                "message": "交钥匙时可以增加一个可见动作。",
                "suggestion": "让她先确认守灯人的手是否伸过来。",
                "evidence": [{"source_quote": "林岚把钥匙交给守灯人。"}],
            }], "uncertainties": [],
        }
        report = save_review(self.conn, self.scope, self.chapter, "repair-run", raw)
        issue = report["findings"][0]
        listed_issue = list_reviews(self.conn, self.scope)[0]["findings"][0]
        self.assertEqual(listed_issue["repair_targets"][0]["evidence_index"], 0)
        self.assertEqual(listed_issue["repair_targets"][0]["source"]["range"]["quote"], "林岚把钥匙交给守灯人。")
        target = review_repair_source(self.conn, self.scope, {
            "review_id": report["id"], "issue_id": issue["id"], "evidence_index": 0,
        })
        self.assertEqual(target["source"]["project_id"], "book")
        self.assertEqual(target["source"]["branch_id"], "main")
        self.assertEqual(target["source"]["chapter_id"], "chapter")
        self.assertEqual(target["source"]["source_revision_id"], "r1")
        self.assertEqual(target["source"]["source_body_hash"], text_hash(self.body))
        self.assertEqual(target["source"]["mode"], "replace")
        self.assertEqual(target["source"]["range"], {
            "start": 0, "end": len("林岚把钥匙交给守灯人。"),
            "quote": "林岚把钥匙交给守灯人。",
        })
        self.assertEqual(target["review_source"], {
            "review_id": report["id"], "issue_id": issue["id"], "evidence_index": 0,
        })
        self.assertEqual(target["issue"]["suggestion"], "让她先确认守灯人的手是否伸过来。")

    def test_review_repair_source_requires_explicit_evidence_for_multiple_passages(self):
        raw = {
            "findings": [{
                "category": "continuity", "severity": "contradiction",
                "message": "两处原句需要一起核对。",
                "evidence": [
                    {"source_chapter_id": "chapter", "source_quote": "林岚把钥匙交给守灯人。"},
                    {"source_chapter_id": "chapter", "source_quote": "守灯人答应在子夜回来。"},
                ],
            }], "uncertainties": [],
        }
        report = save_review(self.conn, self.scope, self.chapter, "repair-two", raw)
        issue = report["findings"][0]
        with self.assertRaisesRegex(ValueError, "明确选择"):
            review_repair_source(self.conn, self.scope, {
                "review_id": report["id"], "issue_id": issue["id"],
            })
        target = review_repair_source(self.conn, self.scope, {
            "review_id": report["id"], "issue_id": issue["id"], "evidence_index": 1,
        })
        self.assertEqual(target["source"]["range"]["quote"], "守灯人答应在子夜回来。")
        self.assertEqual(target["source"]["range"]["start"], len("林岚把钥匙交给守灯人。\n"))
        with self.assertRaisesRegex(ValueError, "不属于"):
            review_repair_source(self.conn, self.scope, {
                "review_id": report["id"], "issue_id": issue["id"], "evidence_index": 2,
            })

    def test_review_repair_source_rejects_ambiguous_quote_without_exact_range(self):
        repeated = "林岚抬头。"
        body = repeated + "\n" + repeated
        self.revise(body)
        chapter = {**self.chapter, "body": body, "source_revision_id": "r2"}
        self.conn.execute("UPDATE document_revision SET canonical_text=? WHERE id='r2'", (body.encode(),))
        raw = {
            "findings": [{
                "category": "style", "severity": "suggestion", "message": "重复句需要明确位置。",
                "evidence": [{"source_quote": repeated}],
            }], "uncertainties": [],
        }
        with self.assertRaisesRegex(ValueError, "ambiguous|不存在|原句"):
            save_review(self.conn, self.scope, chapter, "repair-ambiguous", raw)

    def test_review_repair_source_rejects_changed_manuscript(self):
        raw = {
            "findings": [{
                "category": "style", "severity": "suggestion", "message": "可以具体化动作。",
                "evidence": [{"source_quote": "林岚把钥匙交给守灯人。"}],
            }], "uncertainties": [],
        }
        report = save_review(self.conn, self.scope, self.chapter, "repair-stale", raw)
        issue = report["findings"][0]
        self.revise("林岚把钥匙藏在袖中，独自离开渡口。")
        with self.assertRaises(AnalysisConflict):
            review_repair_source(self.conn, self.scope, {
                "review_id": report["id"], "issue_id": issue["id"], "evidence_index": 0,
            })
        stale = list_reviews(self.conn, self.scope)[0]
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["findings"][0]["repair_targets"], [])

    def test_review_repair_source_rechecks_all_related_passages_and_scope(self):
        past_body = "林岚的左肩伤口已经包扎。"
        self.conn.execute("INSERT INTO document_node VALUES(?,?,?,?,?,?)", ("past", "book", "main", "chapter", "前章", 0))
        self.conn.execute("INSERT INTO document_revision VALUES(?,?,?,?)", ("past-r1", "past", "main", past_body.encode()))
        raw = {
            "findings": [{
                "category": "continuity", "severity": "possible", "message": "肩伤变化需核对。",
                "evidence": [
                    {"source_chapter_id": "past", "source_quote": past_body},
                    {"source_chapter_id": "chapter", "source_quote": "她的左肩仍然疼痛。"},
                ],
            }], "uncertainties": [],
        }
        report = save_review(self.conn, self.scope, self.chapter, "repair-related", raw)
        binding = {"review_id": report["id"], "issue_id": report["findings"][0]["id"], "evidence_index": 1}
        target = review_repair_source(self.conn, self.scope, binding)
        self.assertEqual(target["source"]["chapter_id"], "chapter")
        for foreign_scope in ({**self.scope, "project_id": "other-book"}, {**self.scope, "branch_id": "other-branch"}):
            with self.subTest(scope=foreign_scope), self.assertRaises(ValueError):
                review_repair_source(self.conn, foreign_scope, binding)
        self.conn.execute("INSERT INTO document_revision VALUES(?,?,?,?)", ("past-r2", "past", "main", "前章改成右肩受伤。".encode()))
        with self.assertRaises(AnalysisConflict):
            review_repair_source(self.conn, self.scope, binding)
        stale = list_reviews(self.conn, self.scope)[0]
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["findings"][0]["repair_targets"], [])

    def test_review_repair_source_keeps_exact_second_duplicate_and_emoji_offsets(self):
        repeated = "林岚抬头。"
        prefix = "😀风停了。\n" + repeated + "\n"
        body = prefix + repeated
        self.revise(body)
        chapter = {**self.chapter, "body": body, "source_revision_id": "r2"}
        raw = {
            "findings": [{
                "category": "style", "severity": "suggestion", "message": "第二处重复动作可修订。",
                "evidence": [{"source_quote": repeated, "source_start": len(prefix), "source_end": len(body)}],
            }], "uncertainties": [],
        }
        report = save_review(self.conn, self.scope, chapter, "repair-exact", raw)
        binding = {"review_id": report["id"], "issue_id": report["findings"][0]["id"], "evidence_index": 0}
        target = review_repair_source(self.conn, self.scope, binding)
        self.assertEqual(target["source"]["range"], {"start": len(prefix), "end": len(body), "quote": repeated})
        self.assertEqual(target["issue"]["evidence"][0]["offset_unit"], "unicode_codepoint")
        self.assertEqual(target["source"]["source_body_hash"], text_hash(body))

    def test_review_repair_source_invalid_binding_never_changes_author_decision(self):
        raw = {
            "findings": [{
                "category": "style", "severity": "suggestion", "message": "可以具体化动作。",
                "evidence": [{"source_quote": "她的左肩仍然疼痛。"}],
            }], "uncertainties": [],
        }
        report = save_review(self.conn, self.scope, self.chapter, "repair-validation", raw)
        base = {"review_id": report["id"], "issue_id": report["findings"][0]["id"], "evidence_index": 0}
        for invalid in (None, {}, {**base, "review_id": 1}, {**base, "issue_id": None},
                        {**base, "evidence_index": True}, {**base, "evidence_index": "0"}, {**base, "evidence_index": -1},
                        {**base, "issue_id": "other-issue"}):
            with self.subTest(binding=invalid), self.assertRaises(ValueError):
                review_repair_source(self.conn, self.scope, invalid)
        handled = decide_review(self.conn, self.scope, report["id"], base["issue_id"],
                                {"decision": "accepted", "reason": "准备改写伤势动作"})
        review_repair_source(self.conn, self.scope, base)
        self.assertEqual(handled["findings"][0]["status"], "needs_revision")
        self.assertFalse(handled["auto_pass"])
        self.assertFalse(handled["literary_quality_verified"])
        self.assertEqual(list_reviews(self.conn, self.scope)[0]["status"], "needs_revision")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM document_revision").fetchone()[0], 1)


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisWorkflowTests))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
