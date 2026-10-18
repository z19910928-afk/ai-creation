#!/usr/bin/env python3
"""Real SQLite regressions for agent-managed, explicitly confirmed planning."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import moyu_agent_planning as manager  # noqa: E402
from backend import moyu_agent_service as service  # noqa: E402
from backend import moyu_backups as backups  # noqa: E402
from backend import moyu_settings as settings  # noqa: E402
from backend import server  # noqa: E402


MANUSCRIPT = "林岚握住旧门的把手。\n门里传来两下轻响。"


def candidate_for(stage, blueprint):
    if stage == "overview":
        return {**{key: blueprint[key] for key in ("genre", "audience", "tone", "contract")},
                "premise": blueprint["premise"] + "；她要决定是否交出钥匙。"}
    if stage == "volumes":
        return {"volumes": [{"id": volume["id"], "title": "旧门与钥匙", "summary": "追查第二把锁。"}
                            for volume in blueprint["volumes"]]}
    if stage == "chapters":
        return {"volumes": [
            {"id": volume["id"], "chapters": [
                {"id": chapter["id"], "title": "门后的声音", "goal": "辨认钥匙", "pov": "林岚", "hook": "拖链声响起"}
                for chapter in volume["chapters"]]}
            for volume in blueprint["volumes"]]}
    return {"volumes": [
        {"id": volume["id"], "chapters": [
            {"id": chapter["id"], "scenes": [
                {"id": scene["id"], "title": "门外", "goal": "取回钥匙", "location": "仓库", "pov": "林岚"}
                for scene in chapter["scenes"]]}
            for chapter in volume["chapters"]]}
        for volume in blueprint["volumes"]]}


class AgentPlanningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="moyu-agent-planning-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.previous_db = server.DB_PATH
        self.addCleanup(setattr, server, "DB_PATH", self.previous_db)
        self.environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_AI_DAILY_QUOTA": "0", "MOYU_CREDENTIALS_DIR": str(self.root / "private-keys"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.worker = patch.object(server, "start_ai_worker", side_effect=AssertionError("module must not start workers"))
        self.worker_mock = self.worker.start()
        self.addCleanup(self.worker.stop)
        self.provider = patch.object(server, "call_ai_provider", side_effect=AssertionError("external providers are forbidden"))
        self.provider_mock = self.provider.start()
        self.addCleanup(self.provider.stop)
        server.DB_PATH = self.root / "book.sqlite3"
        server.init_db()
        with closing(server.connect()) as conn, conn:
            server.ensure_seed(conn)
            self.chapter_id = server.chapter_rows(conn)[0]["id"]
            server.update_chapter(conn, self.chapter_id, {"body": MANUSCRIPT})
            current = server.workspace.blueprint_row(conn, server.project_id(), server.branch_id())
            blueprint = current["blueprint"]
            blueprint.update(premise="作者原有蓝图", genre="悬疑", tone="克制", audience="成年读者", contract="有限视角")
            self.blueprint = server.workspace.put_blueprint(
                conn, server.project_id(), server.branch_id(), {"expected_version": current["version"], "blueprint": blueprint})
            settings.save_settings(conn, server.project_id(), {
                "base_url": "http://127.0.0.1:9/v1", "model": "local-free-fixture",
                "api_key": "", "daily_limit": 10, "parameters": {"temperature": 0, "max_tokens": 4000},
            })
        self.revisions = self.manuscript()

    def manuscript(self):
        with closing(server.connect()) as conn:
            return [tuple(row) for row in conn.execute(
                "SELECT id,document_node_id,canonical_text FROM document_revision ORDER BY rowid")]

    def counts(self):
        with closing(server.connect()) as conn:
            return tuple(conn.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in (
                "app_agent_session", "app_ai_job", "ai_run", "app_quota_reservation",
                "app_planning_attempt", "app_planning_application"))

    def database_snapshot(self):
        with closing(server.connect()) as conn:
            return tuple(conn.iterdump()), conn.execute("PRAGMA user_version").fetchone()[0]

    def row(self, conn, session):
        return conn.execute("SELECT * FROM app_agent_session WHERE id=?", (session["id"],)).fetchone()

    def create(self):
        with closing(server.connect()) as conn, conn:
            plan = manager.create(server, conn, self.chapter_id, "规划雨夜码头的第二把锁")
            session_id = server.uid("agent_")
            conn.execute("INSERT INTO app_agent_session VALUES(?,?,?,?,?,?,?,?,?,?,0)", (
                session_id, server.project_id(), server.branch_id(), self.chapter_id,
                server.uid("request_"), "规划雨夜码头的第二把锁", server.json_text(plan),
                plan["summary"]["status"], server.now(), server.now()))
            return manager.public_session(server, conn, self.row(conn, {"id": session_id}))

    def read(self, session):
        with closing(server.connect()) as conn:
            return manager.public_session(server, conn, self.row(conn, session))

    def resume(self, session, action):
        with closing(server.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.row(conn, session)
            result = manager.resume(server, conn, row, {"action": action, "checkpoint_id": session["checkpoint_id"]})
            plan = result["plan"]
            conn.execute("UPDATE app_agent_session SET plan_json=?,status=? WHERE id=?",
                         (server.json_text(plan), plan["summary"]["status"], session["id"]))
            return manager.public_session(server, conn, self.row(conn, session)), result

    def process_candidate(self, session, **changes):
        with closing(server.connect()) as conn:
            workflow = server.planning.get_workflow(
                conn, session["planning_workflow"]["id"], server.project_id(), server.branch_id())

        def generate(task, context, *_args, **_kwargs):
            self.assertIn(task, {"outline", "scene"})
            self.assertEqual(context["planning_workflow"]["stage"], workflow["stage"])
            value = candidate_for(workflow["stage"], context["blueprint"])
            return {
                "mode": "provider", "model": "local-free-fixture", "requested_model": "local-free-fixture",
                "returned_model": "local-free-fixture", "safe_to_apply": True,
                "completion_state": "complete", "finish_reason": "stop", "truncated": False,
                "response_format": "json", "text": server.json_text(value), "structured": value,
                "billing_state": "usage_reported",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, **changes,
            }

        with patch.object(server, "generate_ai", side_effect=generate):
            return server.process_ai_job(session["current_job"]["id"])

    def finish(self, session, **changes):
        self.process_candidate(session, **changes)
        return self.resume(session, "advance")

    def service_create(self, **changes):
        return service.request(server, {
            "request_id": "planning_service_request_1", "chapter_id": self.chapter_id,
            "query": "规划雨夜码头的第二把锁", "mode": "plan", **changes,
        })

    def service_read(self, session):
        with closing(server.connect()) as conn:
            return service.public_session(server, conn, service.get_session(server, conn, session["id"]))

    def service_resume(self, session, action):
        return service.request(server, {
            "action": action, "checkpoint_id": session["checkpoint_id"],
        }, session["id"])

    def service_finish(self, session):
        job = self.process_candidate(session)
        self.assertEqual(job["status"], "success", job)
        service.advance_ready(server, job_id=job["id"])
        return self.service_read(session)

    def ready(self):
        session, _ = self.resume(self.create(), "authorize")
        for stage in server.planning.STAGES:
            self.assertEqual(session["stage"], stage)
            session, _ = self.finish(session)
            self.assertEqual(session["status"], "awaiting_plan_confirmation")
            session, _ = self.resume(session, "confirm")
        self.assertEqual(session["status"], "awaiting_plan_application")
        return session

    def test_invalid_action_types_leave_plan_and_database_unchanged(self):
        session = self.create()
        for value in ([], {}, None, False, True, 0, 1, 1.5, "", "unknown"):
            with self.subTest(value=value):
                before = self.database_snapshot()
                with self.assertRaisesRegex(ValueError, "操作无效"):
                    self.resume(session, value)
                self.assertEqual(self.database_snapshot(), before)
                self.provider_mock.assert_not_called()
                self.worker_mock.assert_not_called()

    def test_create_inherits_blueprint_defaults_and_creates_no_session_or_job(self):
        with closing(server.connect()) as conn:
            plan = manager.create(server, conn, self.chapter_id, "规划下一部悬疑长篇")
            workflow = server.planning.get_workflow(conn, plan["planning_workflow_id"])
            self.assertEqual(workflow["initial"]["premise"], "规划下一部悬疑长篇")
            self.assertEqual(workflow["initial"]["genre"], "悬疑")
            self.assertEqual(workflow["initial"]["tone"], "克制")
            self.assertEqual(workflow["initial"]["counts"], manager.DEFAULT_COUNTS)
            self.assertNotIn("api_key", plan["provider"])
            self.assertEqual(plan["summary"]["calls_used"], 0)
            self.assertTrue(conn.in_transaction)
        self.assertEqual(self.counts(), (0, 0, 0, 0, 0, 0))
        self.worker_mock.assert_not_called()

    def test_existing_dimensions_are_inherited_with_a_bounded_workflow(self):
        with closing(server.connect()) as conn, conn:
            current = server.workspace.blueprint_row(conn, server.project_id(), server.branch_id())
            current["blueprint"]["volumes"] = [
                {"id": f"existing_vol_{index}", "chapters": [
                    {"id": f"existing_chapter_{index}_{chapter}", "scenes": [
                        {"id": f"existing_scene_{index}_{chapter}_{scene}"} for scene in range(6)]}
                    for chapter in range(10)]}
                for index in range(5)]
            server.workspace.put_blueprint(conn, server.project_id(), server.branch_id(), {
                "expected_version": current["version"], "blueprint": current["blueprint"]})
            plan = manager.create(server, conn, self.chapter_id, "整理现有规划")
            workflow = server.planning.get_workflow(conn, plan["planning_workflow_id"])
            self.assertEqual(workflow["initial"]["counts"], manager.COUNT_LIMITS)

    def test_four_confirmed_stages_automatically_queue_next_and_apply_is_explicit(self):
        session = self.create()
        session, result = self.resume(session, "authorize")
        self.assertEqual(len(result["pending"]), 1)
        previous_checkpoint = session["checkpoint_id"]
        for index, stage in enumerate(server.planning.STAGES, 1):
            self.assertEqual(session["stage"], stage)
            session, result = self.finish(session)
            self.assertEqual(session["status"], "awaiting_plan_confirmation")
            self.assertEqual(session["calls_used"], index)
            self.assertEqual(result["pending"], [])
            self.assertNotEqual(session["checkpoint_id"], previous_checkpoint)
            self.assertIsNotNone(session["planning_workflow"]["attempt"]["candidate"])
            session, result = self.resume(session, "confirm")
            self.assertEqual(len(result["pending"]), 0 if index == 4 else 1)
            self.assertEqual(self.manuscript(), self.revisions)
            with closing(server.connect()) as conn:
                self.assertEqual(server.workspace.blueprint_row(conn, server.project_id(), server.branch_id()), self.blueprint)
            previous_checkpoint = session["checkpoint_id"]
        self.assertEqual(session["status"], "awaiting_plan_application")
        self.assertEqual(self.counts(), (1, 4, 4, 4, 4, 0))
        self.assertTrue(all(step["status"] == "confirmed" for step in session["steps"]))
        session, result = self.resume(session, "apply")
        self.assertEqual(session["status"], "review_ready")
        self.assertFalse(session["source_stale"])
        self.assertTrue(session["planning_workflow"]["applied"])
        self.assertEqual(result["pending"], [])
        self.assertEqual(self.counts(), (1, 4, 4, 4, 4, 1))
        self.assertEqual(self.manuscript(), self.revisions)
        self.worker_mock.assert_not_called()
        self.provider_mock.assert_not_called()

    def test_polling_and_repeated_actions_are_idempotent_and_never_confirm(self):
        original = self.create()
        session, result = self.resume(original, "authorize")
        self.assertEqual(len(result["pending"]), 1)
        before = self.counts()
        repeated, result = self.resume(original, "authorize")
        self.assertEqual(repeated, session)
        self.assertEqual(result["pending"], [])
        pending, _ = self.resume(session, "advance")
        self.assertEqual(pending["checkpoint_id"], session["checkpoint_id"])
        self.assertEqual(self.counts(), before)
        session, _ = self.finish(session)
        before = self.counts()
        checkpoint = session["checkpoint_id"]
        for _ in range(3):
            session, result = self.resume(session, "advance")
            self.assertEqual(session["status"], "awaiting_plan_confirmation")
            self.assertEqual(session["checkpoint_id"], checkpoint)
            self.assertEqual(result["pending"], [])
        self.assertEqual(self.counts(), before)
        stale_confirmation = session
        session, _ = self.resume(session, "confirm")
        repeated, result = self.resume(stale_confirmation, "confirm")
        self.assertEqual(repeated, session)
        self.assertEqual(result["pending"], [])
        self.assertEqual(self.counts()[1], 2)

    def test_get_is_read_only_even_after_a_job_has_finished(self):
        session, _ = self.resume(self.create(), "authorize")
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_ai_job SET status='success' WHERE id=?", (session["current_job"]["id"],))
        with closing(server.connect()) as conn:
            before = [tuple(row) for row in conn.execute("SELECT * FROM app_planning_attempt")]
            changes = conn.total_changes
            for _ in range(3):
                value = manager.public_session(server, conn, self.row(conn, session))
                self.assertEqual(value["status"], "awaiting_job")
                self.assertEqual(value["planning_workflow"]["attempt"]["status"], "queued")
            self.assertEqual(conn.total_changes, changes)
            self.assertEqual([tuple(row) for row in conn.execute("SELECT * FROM app_planning_attempt")], before)

    def test_unknown_or_missing_success_billing_stops_without_a_second_call(self):
        for billing in ("unknown", None, ["unknown"]):
            with self.subTest(billing=billing):
                session, _ = self.resume(self.create(), "authorize")
                session, result = self.finish(session, billing_state=billing)
                self.assertEqual(session["status"], "requires_action")
                self.assertIn("费用状态无法确认", session["error"])
                self.assertEqual(session["calls_used"], 1)
                self.assertEqual(result["pending"], [])
                counts = self.counts()
                session, result = self.resume(session, "authorize")
                self.assertEqual(self.counts(), counts)
                self.assertEqual(result["pending"], [])

    def test_known_failed_job_is_terminal_without_a_retry(self):
        session, _ = self.resume(self.create(), "authorize")
        with closing(server.connect()) as conn, conn:
            job = conn.execute("SELECT * FROM app_ai_job WHERE id=?", (session["current_job"]["id"],)).fetchone()
            server.finish_ai_job(conn, job, {"billing_state": "not_dispatched"}, "本机固定失败", 0)
        session, result = self.resume(session, "advance")
        self.assertEqual(session["status"], "requires_action")
        self.assertEqual(session["calls_used"], 1)
        self.assertEqual(self.counts()[1:5], (1, 1, 1, 1))
        self.assertEqual(result["pending"], [])

    def test_blueprint_version_or_content_changes_block_authorization(self):
        for content_only in (False, True):
            with self.subTest(content_only=content_only):
                session = self.create()
                with closing(server.connect()) as conn, conn:
                    current = server.workspace.blueprint_row(conn, server.project_id(), server.branch_id())
                    current["blueprint"]["premise"] += "；作者更改蓝图"
                    if content_only:
                        conn.execute("UPDATE app_blueprint SET payload_json=? WHERE project_id=? AND branch_id=?",
                                     (server.json_text(current["blueprint"]), server.project_id(), server.branch_id()))
                    else:
                        server.workspace.put_blueprint(conn, server.project_id(), server.branch_id(), {
                            "expected_version": current["version"], "blueprint": current["blueprint"]})
                self.assertTrue(self.read(session)["source_stale"])
                session, result = self.resume(session, "authorize")
                self.assertEqual(session["status"], "requires_action")
                self.assertIn("蓝图已变化", session["error"])
                self.assertEqual(result["pending"], [])
        self.assertEqual(self.counts()[1:5], (0, 0, 0, 0))

    def test_blueprint_changes_after_candidate_do_not_confirm_or_queue(self):
        session, _ = self.resume(self.create(), "authorize")
        session, _ = self.finish(session)
        before = self.counts()
        with closing(server.connect()) as conn, conn:
            current = server.workspace.blueprint_row(conn, server.project_id(), server.branch_id())
            current["blueprint"]["premise"] = "作者改了总纲"
            server.workspace.put_blueprint(conn, server.project_id(), server.branch_id(), {
                "expected_version": current["version"], "blueprint": current["blueprint"]})
        session, result = self.resume(session, "confirm")
        self.assertEqual(session["status"], "requires_action")
        self.assertEqual(self.counts(), before)
        self.assertEqual(session["planning_workflow"]["attempt"]["status"], "complete")
        self.assertEqual(result["pending"], [])

    def test_model_changes_block_next_stage_without_consuming_confirmation(self):
        session, _ = self.resume(self.create(), "authorize")
        session, _ = self.finish(session)
        with closing(server.connect()) as conn, conn:
            settings.save_settings(conn, server.project_id(), {"model": "other-local-fixture"})
        session, result = self.resume(session, "confirm")
        self.assertEqual(session["status"], "requires_action")
        self.assertIn("模型设置已变化", session["error"])
        self.assertEqual(session["planning_workflow"]["attempt"]["status"], "complete")
        self.assertEqual(result["pending"], [])
        self.assertEqual(session["calls_used"], 1)

    def test_queue_failure_rolls_back_confirmation_and_its_run_reservation(self):
        session, _ = self.resume(self.create(), "authorize")
        session, _ = self.finish(session)
        before = self.counts()
        with patch.object(server, "reserve_ai_budget", side_effect=ValueError("本机固定额度耗尽")):
            session, result = self.resume(session, "confirm")
        self.assertEqual(session["status"], "requires_action")
        self.assertEqual(session["planning_workflow"]["attempt"]["status"], "complete")
        self.assertEqual(session["planning_workflow"]["stage"], "overview")
        self.assertEqual(self.counts(), before)
        self.assertEqual(result["pending"], [])

    def test_queue_and_plan_changes_are_rolled_back_by_the_callers_transaction(self):
        session = self.create()
        before = self.counts()
        with closing(server.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = manager.resume(server, conn, self.row(conn, session), {
                "action": "authorize", "checkpoint_id": session["checkpoint_id"]})
            self.assertEqual(len(result["pending"]), 1)
            self.assertTrue(conn.in_transaction)
            conn.rollback()
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.read(session), session)

    def test_cancel_queued_and_running_preserves_existing_billing_rules(self):
        for running in (False, True):
            with self.subTest(running=running):
                session, _ = self.resume(self.create(), "authorize")
                run_id = session["current_job"]["ai_run_id"]
                if running:
                    with closing(server.connect()) as conn, conn:
                        conn.execute("UPDATE app_ai_job SET status='running' WHERE ai_run_id=?", (run_id,))
                        conn.execute("UPDATE ai_run SET status='running' WHERE id=?", (run_id,))
                        conn.execute("UPDATE app_quota_reservation SET status='dispatched' WHERE ai_run_id=?", (run_id,))
                session, result = self.resume(session, "cancel")
                self.assertEqual(session["status"], "canceled")
                self.assertTrue(session["current_job"]["cancel_requested"])
                self.assertEqual(len(result["canceled"]), 1)
                with closing(server.connect()) as conn:
                    status = conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?", (run_id,)).fetchone()[0]
                self.assertEqual(status, "dispatched" if running else "released")
                self.assertEqual(session["planning_workflow"]["attempt"]["status"], "unknown" if running else "canceled")

    def test_session_and_workflow_scope_cannot_be_crossed(self):
        session = self.create()
        with closing(server.connect()) as conn, conn:
            other = server.new_project(conn, {"title": "另一作品"})
            other_scope = server.workspace.resolve_scope(conn, other["project"]["id"], other["state"]["branch_id"])
        before = self.counts()
        with server.workspace.use_scope(*other_scope), closing(server.connect()) as conn:
            with self.assertRaises(ValueError):
                manager.public_session(server, conn, self.row(conn, session))
            with self.assertRaises(ValueError):
                manager.resume(server, conn, self.row(conn, session), {
                    "action": "authorize", "checkpoint_id": session["checkpoint_id"]})
        self.assertEqual(self.counts(), before)

    def test_restored_session_is_read_only_and_never_reads_old_workflow_ids(self):
        session, _ = self.resume(self.create(), "authorize")
        with closing(server.connect()) as conn, conn:
            captured = backups.capture(conn, server.project_id())
            restored = backups.transplant(conn, captured, "book_restored_plan_agent")
            scope = server.workspace.resolve_scope(conn, restored["project_id"], restored["branch_id"])
            mapped = restored["identity_map"][session["id"]]
        before = self.counts()
        with server.workspace.use_scope(*scope), closing(server.connect()) as conn:
            with patch.object(server.planning, "get_workflow", side_effect=AssertionError("restored ids must never be read")):
                value = manager.public_session(server, conn, self.row(conn, {"id": mapped}))
                self.assertTrue(value["restored_read_only"])
                self.assertEqual(value["checkpoint_id"], "")
                self.assertIsNone(value["current_job"])
                self.assertIsNone(value["planning_workflow"])
                self.assertFalse(any("job_id" in step or "ai_run_id" in step for step in value["steps"]))
                with self.assertRaisesRegex(ValueError, "仅供回看"):
                    manager.resume(server, conn, self.row(conn, {"id": mapped}), {
                        "action": "advance", "checkpoint_id": session["checkpoint_id"]})
        self.assertEqual(self.counts(), before)

    def test_chapter_edits_do_not_change_or_block_book_planning(self):
        session, _ = self.resume(self.create(), "authorize")
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter_id, {"body": MANUSCRIPT + "\n作者继续写下新的正文。"})
        edited = self.manuscript()
        session, _ = self.finish(session)
        self.assertEqual(session["status"], "awaiting_plan_confirmation")
        self.assertFalse(session["source_stale"])
        session, result = self.resume(session, "confirm")
        self.assertEqual(session["status"], "awaiting_job")
        self.assertEqual(len(result["pending"]), 1)
        self.assertEqual(self.manuscript(), edited)

    def test_service_creation_reuses_request_identity_without_jobs(self):
        created = self.service_create()
        session = created["session"]
        self.assertEqual(session["mode"], "plan")
        self.assertEqual(session["status"], "awaiting_authorization")
        self.assertFalse(created["idempotent"])
        self.assertEqual(self.counts(), (1, 0, 0, 0, 0, 0))
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_planning_workflow").fetchone()[0], 1)
        repeated = self.service_create()
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["session"], session)
        for changes in ({"query": "另一份规划"}, {"mode": "continue"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "这次请求已保存"):
                self.service_create(**changes)
        self.assertEqual(self.counts(), (1, 0, 0, 0, 0, 0))
        self.assertEqual(self.manuscript(), self.revisions)
        self.worker_mock.assert_not_called()
        self.provider_mock.assert_not_called()

    def test_service_authorize_dispatches_once_after_session_and_job_commit(self):
        original = self.service_create()["session"]
        dispatched = []

        def dispatch(job_id):
            session = self.service_read(original)
            self.assertEqual(session["status"], "awaiting_job")
            self.assertEqual(session["current_job"]["id"], job_id)
            self.assertEqual(self.counts(), (1, 1, 1, 1, 1, 0))
            dispatched.append(job_id)
            return False

        with patch.object(server, "start_ai_worker", side_effect=dispatch) as worker:
            result = self.service_resume(original, "authorize")
            self.assertFalse(result["idempotent"])
            repeated = self.service_resume(original, "authorize")
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(repeated["session"], result["session"])
            self.assertEqual(dispatched, [result["session"]["current_job"]["id"]])
            worker.assert_called_once_with(dispatched[0])
        self.provider_mock.assert_not_called()

    def test_service_worker_completion_waits_for_confirmation_then_dispatches_next(self):
        with patch.object(server, "start_ai_worker", return_value=False) as worker:
            session = self.service_resume(self.service_create()["session"], "authorize")["session"]
            session = self.service_finish(session)
            self.assertEqual(session["status"], "awaiting_plan_confirmation")
            self.assertEqual(session["planning_workflow"]["attempt"]["status"], "complete")
            self.assertIsNotNone(session["planning_workflow"]["attempt"]["candidate"])
            self.assertEqual(worker.call_count, 1)
            before = self.counts()
            service.advance_ready(server, job_id=session["current_job"]["id"])
            self.assertEqual(self.service_read(session), session)
            self.assertEqual(self.counts(), before)
            stale = session
            session = self.service_resume(session, "confirm")["session"]
            self.assertEqual(session["status"], "awaiting_job")
            self.assertEqual(session["stage"], "volumes")
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(self.counts(), (1, 2, 2, 2, 2, 0))
            repeated = self.service_resume(stale, "confirm")
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(repeated["session"], session)
            self.assertEqual(worker.call_count, 2)
        self.assertEqual(self.manuscript(), self.revisions)
        self.provider_mock.assert_not_called()

    def test_service_four_confirmations_and_explicit_application_finish_the_plan(self):
        with patch.object(server, "start_ai_worker", return_value=False) as worker:
            session = self.service_resume(self.service_create()["session"], "authorize")["session"]
            for index, stage in enumerate(server.planning.STAGES, 1):
                self.assertEqual(session["stage"], stage)
                session = self.service_finish(session)
                self.assertEqual(session["status"], "awaiting_plan_confirmation")
                self.assertEqual(session["calls_used"], index)
                session = self.service_resume(session, "confirm")["session"]
            self.assertEqual(session["status"], "awaiting_plan_application")
            self.assertEqual(worker.call_count, 4)
            self.assertEqual(self.counts(), (1, 4, 4, 4, 4, 0))
            service.advance_ready(server)
            self.assertEqual(self.service_read(session), session)
            with closing(server.connect()) as conn:
                self.assertEqual(server.workspace.blueprint_row(conn, server.project_id(), server.branch_id()),
                                 self.blueprint)
            stale = session
            result = self.service_resume(session, "apply")
            session = result["session"]
            self.assertFalse(result["idempotent"])
            self.assertEqual(session["status"], "review_ready")
            self.assertTrue(session["planning_workflow"]["applied"])
            self.assertFalse(session["source_stale"])
            self.assertEqual(self.counts(), (1, 4, 4, 4, 4, 1))
            self.assertTrue(self.service_resume(stale, "apply")["idempotent"])
            self.assertEqual(worker.call_count, 4)
        self.assertEqual(self.manuscript(), self.revisions)
        self.provider_mock.assert_not_called()

    def test_service_rechecks_scope_in_the_mutating_transaction(self):
        session = self.service_create()["session"]
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO branch(id,project_id,status,acl_json) VALUES(?,?,?,?)",
                         ("alternate_agent_planning_branch", server.project_id(), "active", "{}"))
            scope = server.workspace.resolve_scope(conn, server.project_id(), "alternate_agent_planning_branch")
        before = self.counts()
        with server.workspace.use_scope(*scope):
            with self.assertRaisesRegex(ValueError, "没有这条写作任务"):
                self.service_resume(session, "authorize")
            with self.assertRaisesRegex(ValueError, "当前作品中的章节"):
                self.service_create(request_id="foreign_branch_plan")
            service.advance_ready(server)
        real_request = service._planning_request

        def change_scope_before_transaction(*args, **kwargs):
            with server.workspace.use_scope(*scope):
                return real_request(*args, **kwargs)

        with patch.object(service, "_planning_request", side_effect=change_scope_before_transaction):
            with self.assertRaisesRegex(ValueError, "没有这条写作任务"):
                self.service_resume(session, "authorize")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.service_read(session), session)
        self.worker_mock.assert_not_called()
        self.provider_mock.assert_not_called()

    def test_service_restored_plan_is_read_only_without_old_reference_reads(self):
        with patch.object(server, "start_ai_worker", return_value=False):
            session = self.service_resume(self.service_create()["session"], "authorize")["session"]
        with closing(server.connect()) as conn, conn:
            restored = backups.transplant(conn, backups.capture(conn, server.project_id()), "book_service_restore")
            scope = server.workspace.resolve_scope(conn, restored["project_id"], restored["branch_id"])
            mapped = restored["identity_map"][session["id"]]
        before = self.counts()
        with server.workspace.use_scope(*scope), patch.object(
            server.planning, "get_workflow", side_effect=AssertionError("restored ids must never be read")
        ):
            restored_session = self.service_read({"id": mapped})
            self.assertTrue(restored_session["restored_read_only"])
            self.assertEqual(restored_session["checkpoint_id"], "")
            self.assertIsNone(restored_session["current_job"])
            self.assertIsNone(restored_session["planning_workflow"])
            for action in ("authorize", "advance", "confirm", "apply", "cancel"):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, "仅供回看"):
                    service.request(server, {
                        "action": action, "checkpoint_id": session["checkpoint_id"],
                    }, mapped)
            service.advance_ready(server)
        self.assertEqual(self.counts(), before)
        self.worker_mock.assert_not_called()
        self.provider_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
