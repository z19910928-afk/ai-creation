#!/usr/bin/env python3
"""Real SQLite/ScopedCheckpointSaver writing-agent service regressions.

All databases are temporary. Workers and the provider transport are patched:
no credential, production database, or paid API is used by this suite.
"""
from __future__ import annotations

from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_agent_service as service  # noqa: E402
import moyu_backups as backups  # noqa: E402
import moyu_settings as settings  # noqa: E402
from moyu_agent_checkpoint import ScopedCheckpointSaver  # noqa: E402
import server  # noqa: E402


MANUSCRIPT = "林岚握住旧门的把手。\n门里传来两下轻响。"


class WritingAgentServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="moyu-writing-agent-")
        self.addCleanup(self.directory.cleanup)
        self.previous_db = server.DB_PATH
        self.addCleanup(setattr, server, "DB_PATH", self.previous_db)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "",
            "MOYU_AI_MODEL": "",
            "MOYU_AI_API_KEY": "",
            "MOYU_CREDENTIALS_DIR": str(self.root / "private-keys"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.real_start_ai_worker = server.start_ai_worker
        self.worker_patch = patch.object(server, "start_ai_worker", return_value=False)
        self.worker = self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.provider_patch = patch.object(
            server, "call_ai_provider", side_effect=AssertionError("external providers are forbidden")
        )
        self.provider = self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)
        server.DB_PATH = self.root / "book.sqlite3"
        server.init_db()
        self.scope = {
            "project_id": server.PROJECT_ID,
            "branch_id": server.BRANCH_ID,
        }
        with closing(server.connect()) as conn, conn:
            server.ensure_seed(conn)
            self.chapter_id = server.chapter_rows(conn)[0]["id"]
            server.update_chapter(conn, self.chapter_id, {"body": MANUSCRIPT})

    def create(self, *, mode="continue", request_id="agent_request_1", **changes):
        payload = {
            "request_id": request_id,
            "chapter_id": self.chapter_id,
            "query": "接着写一段，然后核对章后记忆和故事逻辑。",
            "mode": mode,
            **changes,
        }
        return service.request(server, payload)

    def read(self, session):
        with closing(server.connect()) as conn:
            row = service.get_session(server, conn, session["id"])
            return service.public_session(server, conn, row)

    def resume(self, session, action):
        return service.request(server, {
            "action": action, "checkpoint_id": session["checkpoint_id"],
        }, session["id"])

    def authorize(self, session):
        return self.resume(session, "authorize")["session"]

    def counts(self):
        with closing(server.connect()) as conn:
            return tuple(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in (
                "app_agent_session", "app_agent_checkpoint", "app_agent_write",
                "app_ai_job", "ai_run", "app_quota_reservation",
                "app_chapter_analysis", "app_story_review",
            ))

    def database_snapshot(self):
        with closing(server.connect()) as conn:
            return tuple(conn.iterdump()), conn.execute("PRAGMA user_version").fetchone()[0]

    def manuscript(self):
        with closing(server.connect()) as conn:
            return tuple(tuple(row) for row in conn.execute(
                "SELECT id,document_node_id,canonical_text FROM document_revision ORDER BY rowid"
            ))

    def source(self):
        with closing(server.connect()) as conn:
            return next(c for c in server.chapter_rows(conn) if c["id"] == self.chapter_id)

    def plan(self, session):
        with closing(server.connect()) as conn:
            row = service.get_session(server, conn, session["id"])
            return json.loads(row["plan_json"])

    def offline_continue(self, session):
        job = server.process_ai_job(session["current_job"]["id"])
        self.assertEqual(job["status"], "success", job)
        service.advance_ready(server, job_id=job["id"])
        return self.read(session)

    def finish_analysis(self, session):
        job = session["current_job"]
        task = job["task"]
        with closing(server.connect()) as conn, conn:
            saved = conn.execute("SELECT * FROM app_ai_job WHERE id=?", (job["id"],)).fetchone()
            chapter = next(c for c in server.chapter_rows(conn) if c["id"] == self.chapter_id)
            result = self.structured_result(task, chapter["body"])
            completed = server.finish_ai_job(conn, saved, result, None, 1)
        self.assertEqual(completed["status"], "success", completed)
        self.assertIn("analysis" if task == "chapter_analysis" else "story_review", completed["result"])
        service.advance_ready(server, job_id=job["id"])
        return self.read(session)

    def structured_result(self, task, body):
        if task == "chapter_analysis":
            quote = body.splitlines()[0]
            structured = {
                "summary": {"text": "林岚仍在旧门外。", "evidence": [{"source_quote": quote}]},
                "handoff": {"text": "", "evidence": []},
                "candidates": [],
            }
        elif task == "semantic_review":
            structured = {"findings": [], "uncertainties": ["固定本地用例未评定文学水平。"]}
        else:
            raise AssertionError("unexpected task")
        return {
            "mode": "provider", "model": "fixed-local-result",
            "requested_model": "fixed-local-result", "returned_model": "fixed-local-result",
            "safe_to_apply": True, "completion_state": "complete",
            "finish_reason": "stop", "truncated": False,
            "text": server.json_text(structured), "structured": structured,
            "response_format": "json", "billing_state": "usage_reported",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def adopt(self, session):
        job = session["current_job"]
        source = job["source"]
        result = job["result"]
        chapter = self.source()
        replacement = (("\n\n" if chapter["body"] else "") + result["text"]
                       if job["task"] == "continue" else result["text"])
        with closing(server.connect()) as conn, conn:
            state = server.state_from_db(conn)
            conn.commit()
            applied = server.apply_candidate(conn, job["ai_run_id"], {
                "replacement": replacement,
                "decision": "accepted",
                "range": source["range"],
                "expected_head": state["head_revision"],
            })
        self.assertTrue(applied["applied"])
        service.advance_ready(server, run_id=job["ai_run_id"])
        return self.read(session)

    def test_invalid_mode_types_are_rejected_without_writes_or_calls(self):
        for value in ([], {}, None, False, True, 0, 1, 1.5, "", "unknown"):
            with self.subTest(value=value):
                before = self.database_snapshot()
                with self.assertRaisesRegex(ValueError, "请选择"):
                    self.create(mode=value)
                self.assertEqual(self.database_snapshot(), before)
                self.provider.assert_not_called()
                self.worker.assert_not_called()

    def test_invalid_action_types_are_rejected_without_writes_or_calls(self):
        session = self.create()["session"]
        for value in ([], {}, None, False, True, 0, 1, 1.5, "", "unknown"):
            with self.subTest(value=value):
                before = self.database_snapshot()
                with self.assertRaisesRegex(ValueError, "操作无效"):
                    self.resume(session, value)
                self.assertEqual(self.database_snapshot(), before)
                self.provider.assert_not_called()
                self.worker.assert_not_called()

    def test_invalid_http_inputs_return_400_without_writes_or_calls(self):
        invalid = ([], {}, None, False, True, 0, 1, 1.5, "", "unknown")
        payload = {"request_id": "invalid_http_request", "chapter_id": self.chapter_id,
                   "query": "检查写作任务的输入。"}
        requests = [("/api/writing-agent", {**payload, "mode": value}) for value in invalid]
        for mode in ("continue", "plan"):
            session = self.create(mode=mode, request_id=f"http_{mode}_session")["session"]
            route = f"/api/writing-agent/{session['id']}/resume"
            requests.extend((route, {"action": value, "checkpoint_id": session["checkpoint_id"]})
                            for value in invalid)
            requests.extend((route, {"action": "authorize", "checkpoint_id": value})
                            for value in ([], {}, None, False, True, 0, 1, 1.5, ""))

        class QuietHandler(server.Handler):
            def log_message(self, *_args):
                pass

        app = server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            for route, data in requests:
                with self.subTest(route=route, data=data):
                    before = self.database_snapshot()
                    with closing(http.client.HTTPConnection(*app.server_address, timeout=5)) as connection:
                        connection.request("POST", route, body=json.dumps(data).encode("utf-8"),
                                           headers={"Content-Type": "application/json"})
                        response = connection.getresponse()
                        result = json.loads(response.read())
                    self.assertEqual(response.status, 400, result)
                    self.assertTrue(result.get("error"))
                    self.assertNotIn("unhashable", result["error"])
                    self.assertEqual(self.database_snapshot(), before)
                    self.provider.assert_not_called()
                    self.worker.assert_not_called()
        finally:
            app.shutdown()
            thread.join(timeout=5)
            app.server_close()
        self.assertFalse(thread.is_alive())

    def test_create_and_repeat_are_durable_without_jobs_or_manuscript_edits(self):
        before = self.manuscript()
        created = self.create()
        session = created["session"]
        self.assertEqual(session["status"], "awaiting_authorization")
        self.assertEqual(session["calls_used"], 0)
        self.assertFalse(created["idempotent"])
        counts = self.counts()
        self.assertGreater(counts[1], 0)
        self.assertEqual(counts[3:], (0, 0, 0, 0, 0))
        repeated = self.create()
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["session"], session)
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.manuscript(), before)
        self.worker.assert_not_called()

    def test_conflicting_request_id_does_not_change_original(self):
        session = self.create()["session"]
        counts = self.counts()
        with self.assertRaises(ValueError):
            self.create(query="另一个要求")
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.read(session), session)

    def test_context_evidence_is_frozen_metadata_without_manuscript_or_prompt(self):
        session = self.create()["session"]
        evidence = session["context_evidence"]
        self.assertEqual(evidence["chapter_id"], self.chapter_id)
        self.assertEqual(evidence["source_revision_id"], self.source()["revision_id"])
        self.assertEqual(len(evidence["context_hash"]), 64)
        self.assertIn("confirmed_memory", evidence["counts"])
        self.assertNotIn(MANUSCRIPT, json.dumps(evidence, ensure_ascii=False))
        self.assertNotIn("messages", evidence)
        self.assertFalse(session["source_stale"])
        self.assertEqual(session["result_refs"], {})
        before = self.counts()
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter_id, {"body": MANUSCRIPT + "\n远处亮起一盏灯。"})
        reread = self.read(session)
        self.assertEqual(reread["context_evidence"], evidence)
        self.assertTrue(reread["source_stale"])
        self.assertEqual(self.counts(), before)
        self.provider.assert_not_called()

    def test_empty_inspection_does_not_create_session_or_reserve_calls(self):
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter_id, {"body": " \n\t"})
        before = self.counts()
        with self.assertRaisesRegex(ValueError, "还没有正文"):
            self.create(mode="inspect")
        self.assertEqual(self.counts(), before)
        self.worker.assert_not_called()
        self.provider.assert_not_called()

    def test_empty_and_oversize_polish_do_not_create_session_or_reserve_calls(self):
        for body, message in ((" \n\t", "还没有正文"), ("甲" * 12001, "超过 12000")):
            with self.subTest(length=len(body)):
                with closing(server.connect()) as conn, conn:
                    server.update_chapter(conn, self.chapter_id, {"body": body})
                before = self.counts()
                with self.assertRaisesRegex(ValueError, message):
                    self.create(mode="polish")
                self.assertEqual(self.counts(), before)
        self.worker.assert_not_called()
        self.provider.assert_not_called()

    def test_polish_freezes_full_source_range_in_model_input(self):
        body = "林岚握着门把手，等里面的人开口。\n" * 300
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter_id, {"body": body})
        session = self.authorize(self.create(mode="polish")["session"])
        self.assertEqual(session["max_calls"], 3)
        self.assertEqual([item["task"] for item in session["steps"]],
                         ["polish", "chapter_analysis", "semantic_review"])
        job = session["current_job"]
        self.assertEqual(job["source"]["mode"], "replace")
        self.assertEqual(job["source"]["range"], {"start": 0, "end": len(body), "quote": body})
        with closing(server.connect()) as conn:
            row = conn.execute("SELECT context_json FROM app_ai_job WHERE id=?", (job["id"],)).fetchone()
            context = json.loads(row["context_json"])
        self.assertEqual(context["_source"]["range"]["quote"], body)
        self.assertEqual(context["current_chapter"]["body"], body)
        self.assertEqual(self.source()["body"], body)
        self.provider.assert_not_called()

    def test_polish_adoption_replaces_whole_chapter_then_checks_new_revision(self):
        session = self.authorize(self.create(mode="polish")["session"])
        revised = "林岚攥住门把手，没有推。\n门里又响了两下，比刚才更轻。"
        result = {
            "mode": "provider", "model": "fixed-local-result",
            "requested_model": "fixed-local-result", "returned_model": "fixed-local-result",
            "safe_to_apply": True, "completion_state": "complete",
            "finish_reason": "stop", "truncated": False, "text": revised,
            "response_format": "text", "billing_state": "usage_reported",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        with closing(server.connect()) as conn, conn:
            job = conn.execute("SELECT * FROM app_ai_job WHERE id=?", (session["current_job"]["id"],)).fetchone()
            completed = server.finish_ai_job(conn, job, result, None, 1)
        self.assertEqual(completed["status"], "success", completed)
        service.advance_ready(server, job_id=job["id"])
        session = self.read(session)
        self.assertEqual(session["status"], "awaiting_adoption", session)
        self.assertEqual(self.source()["body"], MANUSCRIPT)
        session = self.adopt(session)
        self.assertEqual(self.source()["body"], revised)
        revision_id = self.source()["revision_id"]
        self.assertEqual(session["current_job"]["source"]["source_revision_id"], revision_id)
        session = self.finish_analysis(session)
        self.assertEqual(session["current_job"]["source"]["source_revision_id"], revision_id)
        session = self.finish_analysis(session)
        self.assertEqual(session["status"], "review_ready")
        self.assertEqual(session["calls_used"], 3)
        self.assertEqual(set(session["result_refs"]), {"chapter_analysis", "semantic_review"})
        self.assertEqual(self.counts()[3:], (3, 3, 0, 1, 1))
        self.provider.assert_not_called()

    def test_offline_polish_advice_never_becomes_whole_chapter_replacement(self):
        session = self.authorize(self.create(mode="polish")["session"])
        completed = server.process_ai_job(session["current_job"]["id"])
        self.assertFalse(completed["result"]["safe_to_apply"])
        service.advance_ready(server, job_id=completed["id"])
        current = self.read(session)
        self.assertEqual(current["status"], "requires_action", current)
        self.assertEqual(self.source()["body"], MANUSCRIPT)
        self.assertEqual(current["calls_used"], 1)
        self.assertEqual(self.counts()[3:6], (1, 1, 0))
        self.provider.assert_not_called()

    def test_completed_session_keeps_both_persisted_artifact_references(self):
        session = self.authorize(self.create(mode="inspect")["session"])
        session = self.finish_analysis(session)
        self.assertIn("chapter_analysis", session["result_refs"])
        session = self.finish_analysis(session)
        self.assertEqual(session["status"], "review_ready")
        self.assertEqual(set(session["result_refs"]), {"chapter_analysis", "semantic_review"})
        self.assertFalse(session["source_stale"])
        for task, reference in session["result_refs"].items():
            self.assertTrue(reference["id"])
            self.assertEqual(reference["ai_run_id"], self.plan(session)["steps"][task]["ai_run_id"])
        current_context = self.plan(session)["summary"]["current_task"]
        self.assertEqual(current_context, "semantic_review")
        self.assertEqual(self.read(session)["context_evidence"], session["context_evidence"])

    def test_authorize_repeat_and_pending_advance_do_not_enqueue_twice(self):
        planned = self.create()["session"]
        started = self.authorize(planned)
        self.assertEqual(started["status"], "awaiting_job")
        self.assertEqual(started["calls_used"], 1)
        counts = self.counts()
        repeat = self.resume(planned, "authorize")
        self.assertTrue(repeat["idempotent"])
        self.assertEqual(self.counts(), counts)
        advanced = self.resume(started, "advance")["session"]
        self.assertEqual(advanced["status"], "awaiting_job")
        self.assertEqual(advanced["current_job"]["id"], started["current_job"]["id"])
        self.assertEqual(self.counts()[3:], counts[3:])
        self.assertEqual(self.worker.call_count, 1)

    def test_model_worker_advance_completes_inspect_with_two_calls_only(self):
        before = self.manuscript()
        session = self.authorize(self.create(mode="inspect")["session"])
        self.assertEqual(session["current_job"]["task"], "chapter_analysis")
        session = self.finish_analysis(session)
        self.assertEqual(session["current_job"]["task"], "semantic_review")
        session = self.finish_analysis(session)
        self.assertEqual(session["status"], "review_ready")
        self.assertEqual(session["calls_used"], 2)
        self.assertEqual(self.counts()[3:], (2, 2, 0, 1, 1))
        self.assertEqual(self.manuscript(), before)
        self.provider.assert_not_called()

    def test_continue_adoption_uses_new_revision_for_analysis_and_review(self):
        before = self.source()["revision_id"]
        session = self.offline_continue(self.authorize(self.create()["session"]))
        self.assertEqual(session["status"], "awaiting_adoption")
        original_run = session["current_job"]["ai_run_id"]
        session = self.adopt(session)
        self.assertEqual(session["current_job"]["task"], "chapter_analysis")
        adopted_revision = self.source()["revision_id"]
        self.assertNotEqual(before, adopted_revision)
        self.assertEqual(session["current_job"]["source"]["source_revision_id"], adopted_revision)
        self.assertEqual(self.plan(session)["summary"]["adoption_run_id"], original_run)
        session = self.finish_analysis(session)
        self.assertEqual(session["current_job"]["source"]["source_revision_id"], adopted_revision)
        session = self.finish_analysis(session)
        self.assertEqual(session["status"], "review_ready")
        self.assertEqual(session["calls_used"], 3)
        self.assertEqual(self.counts()[3:], (3, 3, 0, 1, 1))

    def test_premature_applied_action_preserves_adoption_checkpoint(self):
        session = self.offline_continue(self.authorize(self.create()["session"]))
        before = self.counts()
        with self.assertRaises(ValueError):
            self.resume(session, "applied")
        self.assertEqual(self.read(session), session)
        self.assertEqual(self.counts(), before)

    def test_changed_manuscript_before_authorization_stops_without_job(self):
        session = self.create()["session"]
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter_id, {"body": MANUSCRIPT + "\n作者改过原稿。"})
        result = self.authorize(session)
        self.assertEqual(result["status"], "requires_action")
        self.assertIn("原稿已变化", result["error"])
        self.assertEqual(self.counts()[3:6], (0, 0, 0))
        self.worker.assert_not_called()

    def test_changed_materials_before_authorization_stops_without_job(self):
        session = self.create()["session"]
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_entity_meta SET description='人物现在有另一套背景'")
        result = self.authorize(session)
        self.assertEqual(result["status"], "requires_action")
        self.assertIn("资料已变化", result["error"])
        self.assertEqual(self.counts()[3:6], (0, 0, 0))
        self.worker.assert_not_called()

    def test_changed_source_after_job_finishes_stops_without_followup(self):
        session = self.authorize(self.create(mode="inspect")["session"])
        with closing(server.connect()) as conn, conn:
            job = conn.execute("SELECT * FROM app_ai_job WHERE id=?", (session["current_job"]["id"],)).fetchone()
            conn.execute("UPDATE app_ai_job SET status='success',result_json=? WHERE id=?",
                         (server.json_text({"safe_to_apply": True, "completion_state": "complete",
                                            "analysis": {"id": "old_analysis"}}), job["id"]))
            conn.execute("UPDATE ai_run SET status='success' WHERE id=?", (job["ai_run_id"],))
            server.update_chapter(conn, self.chapter_id, {"body": MANUSCRIPT + "\n作者改过这一句。"})
        result = self.resume(session, "advance")["session"]
        self.assertEqual(result["status"], "requires_action")
        self.assertEqual(self.counts()[3:6], (1, 1, 0))

    def test_unknown_result_is_terminal_and_never_retried(self):
        session = self.authorize(self.create()["session"])
        with closing(server.connect()) as conn, conn:
            job = session["current_job"]
            conn.execute("UPDATE app_ai_job SET status='failed',error='unknown after restart',result_json=? WHERE id=?",
                         (server.json_text({"billing_state": "unknown", "safe_to_apply": False}), job["id"]))
            conn.execute("UPDATE ai_run SET status='unknown' WHERE id=?", (job["ai_run_id"],))
        result = self.resume(session, "advance")["session"]
        self.assertEqual(result["status"], "requires_action")
        before = self.counts()
        repeated = self.resume(result, "advance")
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.worker.call_count, 1)

    def test_missing_or_unrecognized_success_billing_stops_followup(self):
        for index, billing in enumerate(("missing", None, "unrecognized", ["unknown"]), start=1):
            with self.subTest(billing=billing):
                session = self.authorize(self.create(mode="inspect", request_id=f"unknown-billing-{index}")["session"])
                job = session["current_job"]
                result = {"safe_to_apply": True, "completion_state": "complete",
                          "analysis": {"id": f"analysis-{index}"}}
                if billing != "missing":
                    result["billing_state"] = billing
                with closing(server.connect()) as conn, conn:
                    conn.execute("UPDATE app_ai_job SET status='success',result_json=? WHERE id=?",
                                 (server.json_text(result), job["id"]))
                    conn.execute("UPDATE ai_run SET status='success' WHERE id=?", (job["ai_run_id"],))
                service.advance_ready(server, job_id=job["id"])
                stopped = self.read(session)
                self.assertEqual(stopped["status"], "requires_action")
                self.assertIn("费用状态无法确认", stopped["error"])
                self.assertEqual(stopped["calls_used"], 1)
                self.assertEqual(len(stopped["steps"]), 2)
        self.provider.assert_not_called()

    def test_enqueue_failure_rolls_back_ai_run_and_reservation(self):
        session = self.create()["session"]
        with patch.object(server, "reserve_ai_budget", side_effect=ValueError("本机固定预算耗尽")):
            result = self.authorize(session)
        self.assertEqual(result["status"], "requires_action")
        self.assertEqual(result["calls_used"], 0)
        self.assertEqual(self.counts()[3:6], (0, 0, 0))
        self.worker.assert_not_called()

    def test_cancel_queued_job_is_terminal_without_dispatch(self):
        session = self.authorize(self.create()["session"])
        canceled = self.resume(session, "cancel")["session"]
        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(canceled["current_job"]["status"], "canceled")
        self.assertTrue(canceled["current_job"]["cancel_requested"])
        before = self.counts()
        again = self.resume(canceled, "authorize")
        self.assertTrue(again["idempotent"])
        self.assertEqual(self.counts(), before)
        self.provider.assert_not_called()

    def test_cross_project_scope_cannot_resume_or_read_session(self):
        session = self.create()["session"]
        with closing(server.connect()) as conn, conn:
            created = server.new_project(conn, {"title": "另一本书"})
            other = server.workspace.resolve_scope(conn, created["project"]["id"], created["state"]["branch_id"])
        before = self.counts()
        with server.workspace.use_scope(*other):
            with self.assertRaises(ValueError):
                self.resume(session, "authorize")
            with self.assertRaises(ValueError):
                self.read(session)
        self.assertEqual(self.counts(), before)

    def test_foreign_checkpoint_thread_is_rejected(self):
        session = self.create()["session"]
        with closing(server.connect(check_same_thread=False)) as conn:
            saver = ScopedCheckpointSaver(conn, self.scope, session["id"])
            with self.assertRaises(ValueError):
                saver.get_tuple({"configurable": {"thread_id": "foreign-project:foreign-session"}})

    def test_checkpoint_survives_connections_and_threads(self):
        session = self.authorize(self.create()["session"])
        original_job = session["current_job"]["id"]
        results = []
        errors = []

        def resume_in_fresh_thread():
            try:
                results.append(self.resume(session, "advance")["session"])
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=resume_in_fresh_thread)
        thread.start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["current_job"]["id"], original_job)
        self.assertEqual(results[0]["status"], "awaiting_job")
        self.assertEqual(self.counts()[3:6], (1, 1, 0))

    def test_restored_session_checkpoint_is_read_only(self):
        session = self.authorize(self.create()["session"])
        with closing(server.connect()) as conn, conn:
            captured = backups.capture(conn, server.project_id())
            restored = backups.transplant(conn, captured, "book_restored_agent")
            restored_scope = server.workspace.resolve_scope(conn, restored["project_id"], restored["branch_id"])
            mapped = restored["identity_map"][session["id"]]
        before = self.counts()
        with server.workspace.use_scope(*restored_scope):
            restored_session = self.read({"id": mapped})
            self.assertTrue(restored_session["restored_read_only"])
            self.assertEqual(restored_session["checkpoint_id"], "")
            with self.assertRaises(ValueError):
                service.request(server, {"action": "advance", "checkpoint_id": "old_checkpoint"}, mapped)
        self.assertEqual(self.counts(), before)
        self.provider.assert_not_called()

    def test_concurrent_authorization_has_one_job_and_one_worker(self):
        session = self.create()["session"]
        barrier = threading.Barrier(3)
        results = []
        errors = []

        def authorize():
            try:
                barrier.wait(timeout=5)
                results.append(self.resume(session, "authorize"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=authorize) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(item["idempotent"] for item in results), [False, True])
        self.assertEqual({item["session"]["current_job"]["id"] for item in results},
                         {self.read(session)["current_job"]["id"]})
        self.assertEqual(self.counts()[3:6], (1, 1, 0))
        self.assertEqual(self.worker.call_count, 1)

    def test_running_provider_job_restart_becomes_unknown_without_redispatch(self):
        with closing(server.connect()) as conn, conn:
            settings.save_settings(conn, server.project_id(), {
                "base_url": "http://127.0.0.1:9/v1",
                "model": "never-called-local-model",
                "api_key": "",
                "daily_limit": 10,
            })
        session = self.authorize(self.create()["session"])
        job = session["current_job"]
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_ai_job SET status='running' WHERE id=?", (job["id"],))
            conn.execute("UPDATE ai_run SET status='running' WHERE id=?", (job["ai_run_id"],))
            conn.execute("UPDATE app_quota_reservation SET status='dispatched' WHERE ai_run_id=?",
                         (job["ai_run_id"],))
        queued = server.recover_ai_jobs()
        self.assertEqual(queued, [])
        service.advance_ready(server, job_id=job["id"])
        current = self.read(session)
        self.assertEqual(current["status"], "requires_action")
        self.assertEqual(self.counts()[3:6], (1, 1, 1))
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute("SELECT status FROM ai_run WHERE id=?", (job["ai_run_id"],)).fetchone()[0],
                             "unknown")
            self.assertEqual(conn.execute("SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                                          (job["ai_run_id"],)).fetchone()[0], "unknown")
        self.assertEqual(self.worker.call_count, 1)
        self.provider.assert_not_called()

    def test_regular_candidate_apply_before_worker_callback_advances_to_new_revision(self):
        session = self.authorize(self.create()["session"])
        job = server.process_ai_job(session["current_job"]["id"])
        self.assertEqual(job["status"], "success")
        self.assertEqual(self.read(session)["status"], "awaiting_job")
        source = job["source"]
        chapter = self.source()
        with closing(server.connect()) as conn, conn:
            state = server.state_from_db(conn)
            conn.commit()
            applied = server.apply_candidate(conn, job["ai_run_id"], {
                "replacement": ("\n\n" if chapter["body"] else "") + job["result"]["text"],
                "decision": "accepted", "range": source["range"],
                "expected_head": state["head_revision"],
            })
        self.assertTrue(applied["applied"])
        adopted_revision = self.source()["revision_id"]
        service.advance_ready(server, job_id=job["id"])
        current = self.read(session)
        self.assertEqual(current["status"], "awaiting_job", current)
        self.assertEqual(current["current_job"]["task"], "chapter_analysis", current)
        self.assertEqual(current["current_job"]["source"]["source_revision_id"], adopted_revision)
        self.assertEqual(current["calls_used"], 2)
        before = self.counts()
        service.advance_ready(server, job_id=job["id"])
        self.assertEqual(self.counts(), before)

    def test_actual_background_worker_automatically_completes_inspect(self):
        session = self.create(mode="inspect")["session"]
        done = threading.Event()
        errors = []
        original_advance = service.advance_ready

        def generate(task, context, *_args, **_kwargs):
            body = context["current_chapter"]["body"]
            return self.structured_result(task, body)

        def advance(*args, **kwargs):
            try:
                original_advance(*args, **kwargs)
                if self.read(session)["status"] in {"review_ready", "requires_action"}:
                    done.set()
            except Exception as exc:
                errors.append(exc)
                done.set()

        with patch.object(server, "generate_ai", side_effect=generate), \
                patch.object(service, "advance_ready", side_effect=advance), \
                patch.object(server, "start_ai_worker", side_effect=self.real_start_ai_worker):
            self.authorize(session)
            self.assertTrue(done.wait(timeout=10), "background writing workflow did not complete")
            self.assertEqual(errors, [])
            current = self.read(session)
            self.assertEqual(current["status"], "review_ready", current)
            self.assertEqual(current["calls_used"], 2)
            self.assertEqual(self.counts()[3:], (2, 2, 0, 1, 1))
        self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
