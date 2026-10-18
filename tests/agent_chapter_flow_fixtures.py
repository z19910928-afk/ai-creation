#!/usr/bin/env python3
"""Real chat-to-successor HTTP flows using temporary books and a loopback model."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_chat_fixtures import ChatTests, Provider, REPORT_SCOPE
from backend import moyu_backups as backups
from backend import moyu_chapter_transition as transition
from backend import server


BODY = (
    "雨水沿着门框流下，旧屋外只剩一盏灯。她把钥匙放在门槛上，退后两步，等门内的人回答。"
    "木板发出轻响，门里的人却没有走出来。她看了看空着的街道，把灯移到屋檐下面，继续等候。"
)
SOURCE_FILES = (
    "backend/server.py", "backend/moyu_agent_chat.py", "backend/moyu_agent_service.py",
    "backend/moyu_chapter_transition.py", "backend/moyu_chapter_handoff.py", "backend/moyu_backups.py",
    "tests/agent_chat_fixtures.py", "tests/agent_chapter_flow_fixtures.py",
)


def source_fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES}


class SuccessorChatTests(ChatTests):
    """Reuse the isolated HTTP harness; main selects only this class's new cases."""

    def setUp(self):
        super().setUp()
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {
                "body": BODY, "status": "进行中", "chapter_goal": "等待门里的人回答",
                "scene_plan": "门外等待，听到动静，继续守候", "pov": "有限第三人称",
                "next_brief": "次日沿着街道寻找旧屋的主人。",
            })

    def proposal(self, **overrides):
        result = self.complete("next-chapter", **overrides)
        return result["messages"][-1]["actions"][0]

    def commits(self):
        with closing(server.connect()) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM app_chapter_commit WHERE project_id=? AND branch_id=?",
                (self.project, self.branch)).fetchone()[0]

    def assert_unchanged_rejection(self, action, expected=400):
        before, calls = self.snapshot(), len(Provider.calls)
        status, result = self.confirm(action)
        self.assertEqual(status, expected, result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.calls), calls)
        return result

    def test_confirm_archives_and_creates_one_next_chapter_without_generation(self):
        action = self.proposal()
        self.assertEqual(action["label"], "完成本章，打开下一章")
        before, calls = self.counts(), len(Provider.calls)
        self.assertEqual(self.commits(), 0)
        status, result = self.confirm(action)
        self.assertEqual(status, 200, result)
        receipt = result["action"]["result"]
        self.assertTrue(receipt["created"])
        self.assertEqual(receipt["previous_chapter_id"], self.chapter)
        self.assertNotEqual(receipt["chapter_id"], self.chapter)
        self.assertEqual([row["stage"] for row in receipt["commits"]],
                         ["prewrite", "precommit", "postcommit"])
        self.assertTrue(all(row["status"] == "passed" for row in receipt["commits"]))
        with closing(server.connect()) as conn:
            chapters = server.chapter_rows(conn)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["body"], BODY)
        self.assertEqual(chapters[0]["status"], "完成")
        self.assertEqual(chapters[1]["id"], receipt["chapter_id"])
        self.assertEqual(chapters[1]["body"], "")
        self.assertEqual(chapters[1]["pov"], "有限第三人称")
        self.assertEqual(self.commits(), 3)
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(Provider.calls), calls)
        status, repeated = self.confirm(action)
        self.assertEqual(status, 200, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["action"]["result"], receipt)
        self.assertEqual(self.commits(), 3)
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(Provider.calls), calls)

    def test_existing_successor_prose_and_planning_are_preserved(self):
        with closing(server.connect()) as conn, conn:
            following = server.create_chapter(conn, {
                "title": "第二章 已有计划", "body": "次日的街道上，她找到了旧屋的主人。",
                "chapter_goal": "保留人工目标", "scene_plan": "保留人工场景",
                "pov": "屋主", "recap": "保留回顾", "hook": "保留出口",
            })
            existing = dict(next(row for row in server.chapter_rows(conn)
                                 if row["id"] == following))
        action = self.proposal()
        status, result = self.confirm(action)
        self.assertEqual(status, 200, result)
        self.assertFalse(result["action"]["result"]["created"])
        self.assertEqual(result["action"]["result"]["chapter_id"], following)
        with closing(server.connect()) as conn:
            self.assertEqual(next(row for row in server.chapter_rows(conn)
                                  if row["id"] == following), existing)

    def test_chapter_metadata_change_rejects_old_suggestion_without_partial_archive(self):
        action = self.proposal()
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"next_brief": "改为等待屋主来信。"})
        result = self.assert_unchanged_rejection(action)
        self.assertIn("变化", result["error"])
        self.assertEqual(self.commits(), 0)

    def test_catalogue_successor_change_rejects_old_suggestion(self):
        action = self.proposal()
        with closing(server.connect()) as conn, conn:
            server.create_chapter(conn, {"title": "刚插入的下一章", "body": ""})
        result = self.assert_unchanged_rejection(action)
        self.assertIn("变化", result["error"])
        self.assertEqual(self.commits(), 0)

    def test_missing_chapter_plan_blocks_without_marking_complete(self):
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"scene_plan": ""})
        action = self.proposal()
        result = self.assert_unchanged_rejection(action)
        self.assertIn("场景计划", result["error"])
        self.assertEqual(self.commits(), 0)

    def test_unfinished_chapter_blocks_without_any_partial_state(self):
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": "雨水流过门槛。"})
        action = self.proposal()
        result = self.assert_unchanged_rejection(action)
        self.assertIn("60字", result["error"])
        self.assertEqual(self.commits(), 0)

    def test_pending_memory_blocks_even_when_model_suggests_next(self):
        status, pending = self.request("POST", "/api/memory", {
            "id": "next_chat_pending_memory", "name": "门内有人",
            "status": "candidate", "source_chapter_id": self.chapter,
        })
        self.assertEqual(status, 201, pending)
        action = self.proposal()
        result = self.assert_unchanged_rejection(action)
        self.assertIn("记忆", result["error"])
        self.assertEqual(self.commits(), 0)

    def test_archive_failure_rolls_back_chat_action_and_all_chapter_changes(self):
        action = self.proposal()
        original = server.create_chapter_commit

        def block(conn, chapter_id, stage, *args):
            result = original(conn, chapter_id, stage, *args)
            if stage == "postcommit":
                return {**result, "status": "blocked",
                        "review": {"message": "合成的末阶段阻断"}}
            return result

        with patch.object(server, "create_chapter_commit", side_effect=block):
            result = self.assert_unchanged_rejection(action)
        self.assertIn("末阶段阻断", result["error"])
        self.assertEqual(self.commits(), 0)
        status, recovered = self.confirm(action)
        self.assertEqual(status, 200, recovered)
        self.assertEqual(self.commits(), 3)

    def test_concurrent_confirmations_share_one_successor_and_receipt(self):
        action = self.proposal()
        gate, outcomes = threading.Barrier(3), []

        def submit():
            gate.wait(timeout=5)
            outcomes.append(self.confirm(action))

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        gate.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(status == 200 for status, _ in outcomes), outcomes)
        self.assertEqual(len({row["action"]["result"]["chapter_id"]
                              for _, row in outcomes}), 1)
        self.assertEqual(sorted(row["idempotent"] for _, row in outcomes), [False, True])
        self.assertEqual(self.commits(), 3)
        with closing(server.connect()) as conn:
            self.assertEqual(len(server.chapter_rows(conn)), 2)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE event_type=?",
                (transition.EVENT_TYPE,)).fetchone()[0], 1)
        self.assertEqual(len(Provider.calls), 1)

    def test_reference_only_discussion_cannot_offer_next_chapter(self):
        result = self.complete("next-chapter", include_chapter=False,
                               references=[self.reference()])
        self.assertEqual(result["messages"][-1]["actions"], [])
        self.assertEqual(self.commits(), 0)
        self.assertEqual(len(Provider.calls), 1)

    def test_next_chapter_suggestion_is_scoped_to_its_work_and_branch(self):
        action = self.proposal()
        status, other = self.request("POST", "/api/projects", {"title": "其他作品"})
        self.assertEqual(status, 201, other)
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO branch VALUES(?,?,NULL,NULL,NULL,'active','{}')",
                         ("next_chat_other_branch", self.project))
        for scope in (
            {"project": other["project"]["id"], "branch": other["project"]["branch_id"]},
            {"branch": "next_chat_other_branch"},
        ):
            with self.subTest(scope=scope):
                status, valid_scope = self.request("GET", "/api/agent-chat/sessions", **scope)
                self.assertEqual(status, 200, valid_scope)
                before = self.snapshot()
                status, result = self.request(
                    "POST", f"/api/agent-chat/actions/{action['id']}/confirm",
                    {"confirm": True}, **scope)
                self.assertEqual(status, 404, result)
                self.assertIn("创作建议", result["error"])
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.commits(), 0)

    def test_complete_backup_restores_successor_references_read_only(self):
        action = self.proposal()
        status, result = self.confirm(action)
        self.assertEqual(status, 200, result)
        status, backup = self.request("POST", "/api/backups", {})
        self.assertEqual(status, 201, backup)
        raw = (self.root / "backups" / (backup["id"] + ".zip")).read_bytes()
        with closing(server.connect()) as conn, conn:
            archive = backups.read_bytes(raw)
            backups.validate_payload(conn, archive["payload"], manifest=archive["manifest"])
            outcome = backups.transplant(conn, archive["payload"], "restored_next_chat_book")
            restored_project = outcome["project_id"]
            restored_action = conn.execute(
                "SELECT * FROM app_agent_chat_action WHERE project_id=?",
                (restored_project,)).fetchone()
            receipt = json.loads(restored_action["result_json"])
            chapter_ids = {row[0] for row in conn.execute(
                "SELECT id FROM document_node WHERE project_id=? AND kind='chapter'",
                (restored_project,))}
            self.assertEqual(restored_action["kind"], "next_chapter")
            self.assertIn(receipt["chapter_id"], chapter_ids)
            self.assertIn(receipt["next_chapter_id"], chapter_ids)
            self.assertIn(receipt["previous_chapter_id"], chapter_ids)
            self.assertNotEqual(receipt["chapter_id"], result["action"]["result"]["chapter_id"])
            restored_commits = {row[0] for row in conn.execute(
                "SELECT id FROM app_chapter_commit WHERE project_id=?", (restored_project,))}
            self.assertEqual({row["id"] for row in receipt["commits"]}, restored_commits)
            self.assertTrue(restored_commits.isdisjoint(
                row["id"] for row in result["action"]["result"]["commits"]))
            transition_event = conn.execute(
                "SELECT payload_json FROM event_log WHERE project_id=? AND event_type=?",
                (restored_project, transition.EVENT_TYPE)).fetchone()
            self.assertEqual(json.loads(transition_event["payload_json"])["result"], receipt)
            self.assertEqual(conn.execute(
                "SELECT restored_read_only FROM app_agent_chat_session WHERE id=?",
                (restored_action["session_id"],)).fetchone()[0], 1)
            restored_branch = restored_action["branch_id"]
        before, calls = self.snapshot(), len(Provider.calls)
        status, rejected = self.request(
            "POST", f"/api/agent-chat/actions/{restored_action['id']}/confirm",
            {"confirm": True}, project=restored_project, branch=restored_branch)
        self.assertEqual(status, 400, rejected)
        self.assertIn("仅供回看", rejected["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.calls), calls)

    def test_backup_rejects_missing_or_cross_branch_archive_references_without_writes(self):
        action = self.proposal()
        status, result = self.confirm(action)
        self.assertEqual(status, 200, result)
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO branch VALUES(?,?,NULL,NULL,NULL,'active','{}')",
                         ("backup_other_branch", self.project))
            with server.workspace.use_scope(self.project, "backup_other_branch", server.ACTOR_ID):
                other_chapter = server.create_chapter(conn, {
                    "title": "另一分支的章节", "body": BODY,
                    "chapter_goal": "另一分支目标", "scene_plan": "另一分支场景", "pov": "第三人称",
                })
                other_commit = server.create_chapter_commit(conn, other_chapter, "prewrite")
                self.assertEqual(other_commit["status"], "passed")
            payload = backups.capture(conn, self.project)
            backups.validate_payload(conn, payload)
        for problem in ("missing", "cross_branch"):
            with self.subTest(problem=problem):
                altered = json.loads(json.dumps(payload))
                receipt = json.loads(altered["tables"]["app_agent_chat_action"][0]["result_json"])
                if problem == "missing":
                    receipt["commits"][0]["id"] = "missing_archive_reference"
                else:
                    receipt["commits"][0]["id"] = other_commit["id"]
                altered["tables"]["app_agent_chat_action"][0]["result_json"] = server.json_text(receipt)
                before = self.snapshot()
                with closing(server.connect()) as conn:
                    message = "JSON对象跨越" if problem == "cross_branch" else "JSON对象身份"
                    with self.assertRaisesRegex(ValueError, message):
                        backups.validate_payload(conn, altered)
                self.assertEqual(self.snapshot(), before)


def main():
    started = datetime.now(timezone.utc).isoformat()
    before = source_fingerprints()
    suite = unittest.TestSuite(SuccessorChatTests(name)
                               for name in sorted(SuccessorChatTests.__dict__)
                               if name.startswith("test_"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    after = source_fingerprints()
    report = {
        "passed": result.wasSuccessful(), "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "local_provider_calls": SuccessorChatTests.total_local_calls,
        "paid_provider_calls": 0, "real_database_access": False, "scope": REPORT_SCOPE,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "runtime": sys.version.split()[0], "source_sha256_before": before,
        "source_sha256_after": after, "source_unchanged": before == after,
    }
    target = os.environ.get("MOYU_AGENT_CHAPTER_REPORT")
    if target:
        output = Path(target)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] and report["source_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
