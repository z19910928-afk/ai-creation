#!/usr/bin/env python3
"""Atomic next-chapter regressions with temporary SQLite and no model calls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_analysis
import moyu_chapter_transition as transition
import server


BODY = (
    "林岚握住旧门的把手，冰冷的金属让她停了一瞬。\n"
    "门里传来两下轻响，她没有立刻推门，而是把油灯放在了台阶上。\n"
    "窗边的影子随即消失，潮湿的门缝中落下一张写着日期的船票。\n"
    "她捡起船票，看见背面的字迹正是失踪兄长的笔迹。"
)
QUOTE = BODY.splitlines()[0]
PLAN = {"chapter_goal": "辨认门里的声音", "scene_plan": "门外试探，拿到船票，发现兄长线索",
        "pov": "林岚", "next_brief": "沿着船票上的日期寻找兄长"}


class ChapterTransitionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="moyu-chapter-transition-")
        self.addCleanup(directory.cleanup)
        previous = server.DB_PATH
        self.addCleanup(setattr, server, "DB_PATH", previous)
        server.DB_PATH = Path(directory.name) / "transition.sqlite3"
        environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_CREDENTIALS_DIR": str(Path(directory.name) / "keys"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("call_ai_provider", "start_ai_worker", "generate_ai", "create_ai_job"):
            blocker = patch.object(server, name, side_effect=AssertionError("model calls are forbidden"))
            mocked = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mocked.assert_not_called)
        server.init_db()
        self.project, self.branch = server.PROJECT_ID, server.BRANCH_ID
        with closing(server.connect()) as conn, conn:
            self.chapter = server.chapter_rows(conn)[0]["id"]
            server.update_chapter(conn, self.chapter, {
                "body": BODY, "status": "进行中", **PLAN,
            })

    def source(self, chapter_id=None):
        with closing(server.connect()) as conn:
            return transition.capture_source(server, conn, chapter_id or self.chapter)

    def run_transition(self, *, source=None, request_id="transition_fixture_request",
                       chapter_id=None):
        expected = source if source is not None else self.source(chapter_id)
        with closing(server.connect()) as conn, conn:
            return transition.complete_and_open_next(
                server, conn, chapter_id or self.chapter,
                expected_source=expected, request_id=request_id)

    def snapshot(self):
        with closing(server.connect()) as conn:
            return tuple(conn.iterdump())

    def chapters(self):
        with closing(server.connect()) as conn:
            return server.chapter_rows(conn)

    def patch_chapter(self, chapter_id=None, **values):
        with closing(server.connect()) as conn, conn:
            self.assertTrue(server.update_chapter(conn, chapter_id or self.chapter, values))

    def add_chapter(self, **values):
        with closing(server.connect()) as conn, conn:
            return server.create_chapter(conn, {"title": "已规划的下一章", "body": "", **values})

    def assert_rejected_without_writes(self, source, exception=ValueError, **options):
        before = self.snapshot()
        with self.assertRaises(exception):
            self.run_transition(source=source, **options)
        self.assertEqual(self.snapshot(), before)

    def memory(self, *, chapter_id=None, status="candidate", payload_has_source=True,
               source_status="valid"):
        chapter_id = chapter_id or self.chapter
        with closing(server.connect()) as conn, conn:
            chapter = next(item for item in server.chapter_rows(conn) if item["id"] == chapter_id)
            quote = chapter["body"].splitlines()[0]
            payload = {"name": "船票线索", "source_chapter_id": chapter_id, "source_quote": quote,
                       "source_start": 0, "source_end": len(quote)}
            span = server.source_span_for_memory(conn, payload)
            self.assertIsNotNone(span)
            if not payload_has_source:
                payload.pop("source_chapter_id")
            identifier = server.uid("memory_")
            conn.execute(
                "INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,kind,"
                "payload_json,status,confidence,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (identifier, self.project, self.branch, span, "线索", server.json_text(payload),
                 status, 0.8, server.now(), server.ACTOR_ID))
            conn.execute("UPDATE source_span SET status=? WHERE id=?", (source_status, span))
            return identifier

    def test_capture_is_read_only_and_contains_no_manuscript(self):
        before = self.snapshot()
        source = self.source()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(source), transition.SOURCE_FIELDS)
        self.assertNotIn(BODY, server.json_text(source))
        self.assertEqual(source["source_body_hash"], server.hash_text(BODY))

    def test_new_successor_archives_current_and_preserves_body(self):
        prior = self.chapters()[0]
        result = self.run_transition()
        current, successor = self.chapters()
        self.assertEqual(current["id"], self.chapter)
        self.assertEqual(current["body"], prior["body"])
        self.assertEqual(current["revision_id"], prior["revision_id"])
        self.assertEqual(current["status"], "完成")
        self.assertEqual(successor["id"], result["chapter_id"])
        self.assertEqual(result["next_chapter_id"], successor["id"])
        self.assertEqual(successor["body"], "")
        self.assertEqual(successor["pov"], PLAN["pov"])
        self.assertEqual(successor["chapter_goal"], "")
        self.assertEqual(successor["scene_plan"], "")
        self.assertTrue(result["created"])
        self.assertEqual([item["stage"] for item in result["commits"]],
                         ["prewrite", "precommit", "postcommit"])
        self.assertTrue(all(item["status"] == "passed" for item in result["commits"]))

    def test_existing_successor_keeps_manuscript_plan_and_structure(self):
        successor = self.add_chapter(
            body="下一章已有正文，不能被覆盖。", chapter_goal="原定目标", scene_plan="原定场景",
            pov="兄长", hook="原定钩子", next_brief="原定交接")
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO app_chapter_structure VALUES(?,?,?,?,?,?,?)",
                         (successor, self.project, self.branch, "volume_fixture", "plan_fixture",
                          '[{"id":"scene_fixture","goal":"原有计划"}]', server.now()))
        before = deepcopy(self.chapters()[1])
        result = self.run_transition()
        self.assertFalse(result["created"])
        self.assertEqual(result["chapter_id"], successor)
        self.assertEqual(self.chapters()[1], before)

    def test_immediate_completed_successor_is_not_skipped_or_changed(self):
        immediate = self.add_chapter(body="既有第二章。", status="完成")
        self.add_chapter(body="既有第三章。", status="草稿")
        before = deepcopy(self.chapters()[1:])
        result = self.run_transition()
        self.assertEqual(result["chapter_id"], immediate)
        self.assertEqual(self.chapters()[1:], before)

    def test_same_request_replays_without_duplicate_commits_or_chapters(self):
        source = self.source()
        first = self.run_transition(source=source)
        before = self.snapshot()
        second = self.run_transition(source=source)
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["chapter_id"], first["chapter_id"])
        self.assertEqual(self.snapshot(), before)

    def test_same_request_with_changed_source_is_rejected(self):
        source = self.source()
        self.run_transition(source=source)
        changed = {**source, "source_body_hash": "f" * 64}
        self.assert_rejected_without_writes(changed, transition.TransitionConflict)

    def test_new_request_for_completed_chapter_reuses_successor(self):
        first = self.run_transition()
        second = self.run_transition(request_id="separate_transition_request")
        self.assertEqual(second["chapter_id"], first["chapter_id"])
        self.assertFalse(second["created"])
        self.assertEqual(len(self.chapters()), 2)

    def test_stale_body_is_rejected(self):
        source = self.source()
        self.patch_chapter(body=BODY + "\n作者又补了一句。")
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_stale_handoff_and_each_plan_field_are_rejected(self):
        for field in PLAN:
            with self.subTest(field=field):
                source = self.source()
                self.patch_chapter(**{field: PLAN[field] + "新的安排"})
                self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_changed_catalogue_is_rejected(self):
        source = self.source()
        self.add_chapter()
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_reordered_catalogue_is_rejected_without_revision_change(self):
        successor = self.add_chapter()
        source = self.source()
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE document_node SET ordinal=-1 WHERE id=?", (successor,))
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_changed_successor_plan_is_rejected(self):
        successor = self.add_chapter(chapter_goal="原有下一章任务")
        source = self.source()
        self.patch_chapter(successor, chapter_goal="作者更改后的下一章任务")
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_changed_blueprint_version_is_rejected(self):
        source = self.source()
        with closing(server.connect()) as conn, conn:
            current = server.workspace.blueprint_row(conn, self.project, self.branch)
            server.workspace.put_blueprint(conn, self.project, self.branch, {
                "expected_version": current["version"],
                "blueprint": {**current["blueprint"], "premise": "新的蓝图"},
            })
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_new_memory_after_discussion_is_rejected(self):
        source = self.source()
        self.memory(status="rejected")
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_pending_memory_blocks_without_partial_archive(self):
        self.memory()
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)

    def test_source_span_bound_memory_without_payload_id_still_blocks(self):
        self.memory(payload_has_source=False)
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)

    def test_accepted_memory_with_stale_evidence_blocks(self):
        self.memory(status="accepted", source_status="stale")
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)

    def test_rejected_memory_does_not_block(self):
        self.memory(status="rejected")
        self.assertTrue(self.run_transition()["created"])

    def test_other_chapter_pending_memory_does_not_block(self):
        successor = self.add_chapter(body=BODY + "\n下一章额外原句。")
        self.memory(chapter_id=successor)
        self.assertEqual(self.run_transition()["chapter_id"], successor)

    def test_deleted_chapter_is_rejected(self):
        source = self.source()
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO app_deleted_node VALUES(?,?,?)",
                         (self.chapter, server.now(), "temporary fixture deletion"))
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_other_project_source_is_rejected(self):
        source = self.source()
        with closing(server.connect()) as conn, conn:
            other = server.new_project(conn, {"title": "独立作品"})
        before = self.snapshot()
        project = other["project"]
        with server.workspace.use_scope(project["id"], project["branch_id"],
                                        other["state"]["chapters"][0]["project_id"]):
            with closing(server.connect()) as conn, conn:
                with self.assertRaises(transition.TransitionConflict):
                    transition.complete_and_open_next(
                        server, conn, self.chapter, expected_source=source,
                        request_id="transition_fixture_request")
        self.assertEqual(self.snapshot(), before)

    def test_missing_extra_and_wrong_source_version_are_rejected(self):
        source = self.source()
        for invalid in ({}, {key: value for key, value in source.items() if key != "commits_hash"},
                        {**source, "unsupported": "value"}, {**source, "version": True}):
            with self.subTest(source_keys=sorted(invalid)):
                self.assert_rejected_without_writes(invalid, transition.TransitionConflict)

    def test_completed_book_blocks(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_project_meta SET book_status='complete' WHERE project_id=?",
                         (self.project,))
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)

    def test_archived_project_blocks(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO app_workspace VALUES(?,1,?,?)",
                         (self.project, server.now(), server.now()))
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)

    def test_inactive_branch_blocks(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE branch SET status='archived' WHERE id=?", (self.branch,))
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)

    def test_short_manuscript_blocks(self):
        self.patch_chapter(body="不足六十字的原稿。")
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)

    def test_missing_plan_fields_block(self):
        for field in ("chapter_goal", "scene_plan", "pov"):
            with self.subTest(field=field):
                self.patch_chapter(**{field: " "})
                self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)
                self.patch_chapter(**{field: PLAN[field]})

    def test_unresolved_story_review_blocks_without_partial_archive(self):
        with closing(server.connect()) as conn, conn:
            context, chapter = server.ai_context(conn, self.chapter, task="semantic_review")
            moyu_analysis.save_review(conn, {
                "project_id": self.project, "branch_id": self.branch, "sent_context": context,
            }, chapter, None, {
                "findings": [{"category": "continuity", "severity": "possible",
                              "message": "作者尚未决定的问题",
                              "evidence": [{"source_chapter_id": self.chapter, "source_quote": QUOTE}]}],
                "uncertainties": [],
            })
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)

    def test_failure_at_each_commit_stage_rolls_back_all_service_writes(self):
        original = server.create_chapter_commit
        for stage in ("prewrite", "precommit", "postcommit"):
            with self.subTest(stage=stage):
                source = self.source()

                def fail_after_insert(conn, chapter_id, current_stage):
                    result = original(conn, chapter_id, current_stage)
                    if current_stage == stage:
                        result["status"] = "blocked"
                    return result

                with patch.object(server, "create_chapter_commit", side_effect=fail_after_insert):
                    self.assert_rejected_without_writes(source, transition.TransitionBlocked)

    def test_successor_creation_failure_rolls_back_status_review_and_commits(self):
        source = self.source()
        with patch.object(server, "create_chapter", side_effect=RuntimeError("fixture failure")):
            self.assert_rejected_without_writes(source, RuntimeError)

    def test_outer_transaction_is_not_committed(self):
        source, before = self.source(), self.snapshot()
        with closing(server.connect()) as conn:
            result = transition.complete_and_open_next(
                server, conn, self.chapter, expected_source=source,
                request_id="transition_fixture_request")
            self.assertTrue(conn.in_transaction)
            with closing(server.connect()) as observer:
                self.assertIsNone(observer.execute(
                    "SELECT id FROM document_node WHERE id=?", (result["chapter_id"],)).fetchone())
            conn.rollback()
        self.assertEqual(self.snapshot(), before)

    def test_failure_preserves_earlier_outer_transaction_changes(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE project SET name='earlier outer change' WHERE id=?", (self.project,))
            source = transition.capture_source(server, conn, self.chapter)
            original = server.create_chapter_commit

            def fail_after_insert(connection, chapter_id, stage):
                result = original(connection, chapter_id, stage)
                if stage == "precommit":
                    result["status"] = "blocked"
                return result

            with patch.object(server, "create_chapter_commit", side_effect=fail_after_insert):
                with self.assertRaises(transition.TransitionBlocked):
                    transition.complete_and_open_next(
                        server, conn, self.chapter, expected_source=source,
                        request_id="transition_fixture_request")
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT name FROM project WHERE id=?",
                                          (self.project,)).fetchone()[0], "earlier outer change")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_chapter_commit").fetchone()[0], 0)
        self.assertEqual(self.chapters()[0]["status"], "进行中")

    def test_parallel_same_request_creates_one_successor_and_one_receipt(self):
        source = self.source()

        def execute(_index):
            return self.run_transition(source=source)

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(execute, range(6)))
        self.assertEqual(len({result["chapter_id"] for result in results}), 1)
        self.assertEqual(sum(not result["idempotent"] for result in results), 1)
        self.assertEqual(len(self.chapters()), 2)
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE event_type=?",
                (transition.EVENT_TYPE,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_chapter_commit").fetchone()[0], 3)

    def test_chat_queue_events_do_not_make_chapter_source_stale(self):
        source = self.source()
        with closing(server.connect()) as conn, conn:
            server.append_event(conn, "synthetic_chat_run", "ai_run.queued",
                                {"task": "assistant_chat"})
        self.assertEqual(self.source(), source)
        self.assertTrue(self.run_transition(source=source)["created"])

    def test_repeating_basic_review_does_not_change_source(self):
        source = self.source()
        with closing(server.connect()) as conn, conn:
            server.review_chapter(conn, self.chapter)
        self.assertEqual(self.source(), source)
        self.assertTrue(self.run_transition(source=source)["created"])

    def test_new_archive_record_makes_source_stale(self):
        source = self.source()
        with closing(server.connect()) as conn, conn:
            server.create_chapter_commit(conn, self.chapter, "prewrite")
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_deleted_successor_does_not_create_another_on_receipt_replay(self):
        source = self.source()
        result = self.run_transition(source=source)
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO app_deleted_node VALUES(?,?,?)",
                         (result["chapter_id"], server.now(), "temporary fixture deletion"))
        self.assert_rejected_without_writes(source, transition.TransitionConflict)

    def test_unmaterialized_planned_successor_blocks_without_duplicate_chapter(self):
        with closing(server.connect()) as conn, conn:
            current = server.workspace.blueprint_row(conn, self.project, self.branch)
            blueprint = {**current["blueprint"], "volumes": [{
                "id": "fixture_volume", "title": "第一卷", "summary": "", "chapters": [
                    {"id": "fixture_plan_one", "title": "第一章", "chapter_id": self.chapter,
                     "goal": PLAN["chapter_goal"], "pov": PLAN["pov"], "hook": "", "scenes": []},
                    {"id": "fixture_plan_two", "title": "计划中的第二章",
                     "goal": "规划原有目标", "pov": "另一视角", "hook": "", "scenes": []},
                ],
            }]}
            server.workspace.put_blueprint(conn, self.project, self.branch, {
                "expected_version": current["version"], "blueprint": blueprint,
            })
            conn.execute("INSERT INTO app_chapter_structure VALUES(?,?,?,?,?,?,?)",
                         (self.chapter, self.project, self.branch, "fixture_volume",
                          "fixture_plan_one", "[]", server.now()))
        self.assert_rejected_without_writes(self.source(), transition.TransitionBlocked)


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ChapterTransitionTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report_path = os.environ.get("MOYU_CHAPTER_TRANSITION_REPORT")
    if report_path:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Temporary SQLite, real archive/review/next-chapter functions, "
                     "transaction and concurrency regressions; no author data or model calls.",
            "passed": result.wasSuccessful(),
            "tests_run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "paid_calls": 0,
            "source_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ("moyu_chapter_transition.py", "tests/chapter_transition_fixtures.py",
                             "server.py", "moyu_workspace.py", "moyu_analysis.py")
            },
        }
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
