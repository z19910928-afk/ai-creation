#!/usr/bin/env python3
"""Whole-book author inbox regressions using temporary SQLite data and no models."""
from __future__ import annotations

from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import moyu_analysis
from backend import moyu_inbox
from backend import moyu_workspace
from backend import server


BODY = "林岚握住旧门的把手。\n门里传来两下轻响。"
QUOTE = BODY.splitlines()[0]


class WritingInboxTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="moyu-inbox-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "inbox.sqlite3"
        previous = server.DB_PATH
        self.addCleanup(setattr, server, "DB_PATH", previous)
        server.DB_PATH = self.path
        environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_CREDENTIALS_DIR": str(Path(directory.name) / "keys"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("call_ai_provider", "start_ai_worker"):
            blocker = patch.object(server, name, side_effect=AssertionError("model calls are forbidden"))
            mocked = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mocked.assert_not_called)
        server.init_db()
        self.project, self.branch = server.PROJECT_ID, server.BRANCH_ID
        self.scope = {"project_id": self.project, "branch_id": self.branch}
        self.sequence = 0
        with closing(server.connect()) as conn, conn:
            server.ensure_seed(conn)
            self.chapter = server.chapter_rows(conn)[0]["id"]
            server.update_chapter(conn, self.chapter, {"body": BODY})
            self.context, self.current = server.ai_context(conn, self.chapter, task="continue")
            self.context["_source"] = server.candidate_source(conn, "continue", self.current)

    def inbox(self, **parameters):
        with closing(server.connect()) as conn:
            with moyu_workspace.use_scope(self.project, self.branch, server.ACTOR_ID):
                return moyu_inbox.writing_inbox(
                    conn, self.project, self.branch,
                    authorization_context=lambda chapter_id, **options: server.ai_context(
                        conn, chapter_id, **options)[0],
                    **parameters)

    def snapshot(self):
        with closing(server.connect()) as conn:
            return tuple(conn.iterdump()), conn.execute("PRAGMA user_version").fetchone()[0]

    def job(self, *, status="success", decision="pending", run_status=None, context=None,
            task="continue", result=None, consent=None):
        self.sequence += 1
        job_id, run_id = f"inbox_job_{self.sequence:04}", f"inbox_run_{self.sequence:04}"
        context = copy.deepcopy(self.context if context is None else context)
        raw_context = server.json_text(context)
        with closing(server.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO ai_run(id,project_id,branch_id,mode,provider,model,"
                "system_prompt_hash,prompt_hash,input_hash,started_at,status,author_decision,consent_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, self.project, self.branch, "fixture", "local", "synthetic",
                 "fixed", "fixed", server.hash_text(raw_context), server.now(),
                 run_status or status, decision, server.json_text(consent or {})),
            )
            conn.execute(
                "INSERT INTO app_ai_job(id,project_id,branch_id,ai_run_id,task,chapter_id,"
                "instruction,context_json,response_format,max_tokens,status,result_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, self.project, self.branch, run_id, task, self.chapter,
                 "PRIVATE_INSTRUCTION_DO_NOT_RETURN", raw_context, "text", 400, status,
                 server.json_text(result if result is not None else {
                     "text": "PRIVATE_CANDIDATE_DO_NOT_RETURN", "safe_to_apply": True,
                     "completion_state": "complete", "finish_reason": "stop", "truncated": False,
                 }),
                 "2026-10-16T00:00:00+00:00", "2026-10-16T00:00:00+00:00"),
            )
        return job_id, run_id

    def session(self, *, status="awaiting_authorization", job_id=None, restored=False, source=None):
        self.sequence += 1
        session_id = f"inbox_agent_{self.sequence:04}"
        plan = {
            "mode": "continue",
            "source": source or {"source_revision_id": self.current["revision_id"]},
            "summary": {"current_job_id": job_id or "", "stage": "continue"},
            "query": "PRIVATE_AGENT_QUERY_DO_NOT_RETURN",
        }
        with closing(server.connect()) as conn, conn:
            context, _ = server.ai_context(
                conn, self.chapter, task="continue", instruction=plan["query"])
            plan["context_hash"] = server.hash_text(server.json_text(context))
            conn.execute(
                "INSERT INTO app_agent_session(id,project_id,branch_id,chapter_id,request_id,"
                "query,plan_json,status,created_at,updated_at,restored_read_only) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, self.project, self.branch, self.chapter, session_id,
                 "PRIVATE_AGENT_QUERY_DO_NOT_RETURN", server.json_text(plan), status,
                 "2026-10-16T00:00:00+00:00", "2026-10-16T00:00:00+00:00", int(restored)),
            )
        return session_id

    def independent_memory(self, identifier="inbox_bound_memory"):
        with closing(server.connect()) as conn, conn:
            chapter = next(row for row in server.chapter_rows(conn) if row["id"] == self.chapter)
            payload = {
                "name": "PRIVATE_MEMORY_TEXT", "source_chapter_id": self.chapter,
                "source_revision_id": chapter["revision_id"], "source_quote": QUOTE,
                "source_start": 0, "source_end": len(QUOTE), "source_hash": server.hash_text(QUOTE),
                "chapter_hash": server.hash_text(chapter["body"]),
            }
            span = server.source_span_for_memory(conn, payload)
            self.assertIsNotNone(span)
            payload["source_span_id"] = span
            conn.execute(
                "INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,kind,payload_json,"
                "status,confidence,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (identifier, self.project, self.branch, span, "人物", server.json_text(payload),
                 "candidate", 0.5, server.now(), server.ACTOR_ID),
            )
        return span, payload

    def report(self, *, empty=False, message="合成审稿问题"):
        with closing(server.connect()) as conn, conn:
            context, chapter = server.ai_context(conn, self.chapter, task="semantic_review")
            return moyu_analysis.save_review(conn, {**self.scope, "sent_context": context}, chapter, None, {
                "findings": [] if empty else [{
                    "category": "continuity", "severity": "possible", "message": message,
                    "evidence": [{"source_chapter_id": self.chapter, "source_quote": QUOTE}],
                }],
                "uncertainties": ["仅验证任务统计，不评价文学质量。"],
            })

    def decide_report(self, report, decision):
        with closing(server.connect()) as conn, conn:
            current = next(item for item in moyu_analysis.list_reviews(conn, self.scope)
                           if item["id"] == report["id"])
            return moyu_analysis.decide_review(
                conn, self.scope, report["id"], report["findings"][0]["id"],
                {"decision": decision, "reason": "作者已检查当前稿件，确认这是有意的安排。",
                 "resolution_source": current.get("author_resolution_source")},
            )

    def analysis(self):
        with closing(server.connect()) as conn, conn:
            context, chapter = server.ai_context(conn, self.chapter, task="chapter_analysis")
            return moyu_analysis.save_analysis(conn, {**self.scope, "sent_context": context}, chapter, None, {
                "summary": {"text": "林岚仍在旧门外。", "evidence": [{"source_quote": QUOTE}]},
                "handoff": {"text": "", "evidence": []}, "candidates": [],
            })

    def test_full_counts_above_previous_queue_and_session_caps(self):
        for _ in range(231):
            self.job()
        for _ in range(67):
            self.session()
        result = self.inbox(limit=25)
        self.assertEqual(result["counts"], {"agent": 67, "queue": 231, "analysis": 0, "review": 1})
        self.assertEqual(result["total"], 299)
        self.assertEqual(result["total_all"], 299)
        self.assertEqual(len(result["items"]), 25)
        self.assertTrue(result["has_more"])
        queue = self.inbox(kind="queue", limit=100)
        self.assertEqual(queue["total"], 231)
        self.assertEqual(queue["total_all"], 299)
        self.assertEqual(queue["counts"], result["counts"])

    def test_pages_have_no_duplicates_or_missing_items_with_tied_timestamps(self):
        for _ in range(207):
            self.job()
        for _ in range(53):
            self.session()
        ids = []
        total = self.inbox()["total"]
        for offset in range(0, total, 17):
            page = self.inbox(offset=offset, limit=17)
            ids.extend(item["id"] for item in page["items"])
            self.assertEqual(page["total"], total)
        self.assertEqual(len(ids), total)
        self.assertEqual(len(set(ids)), total)
        repeat = [item["id"] for offset in range(0, total, 100)
                  for item in self.inbox(limit=100, offset=offset)["items"]]
        self.assertEqual(ids, repeat)
        beyond = self.inbox(offset=total + 500)
        self.assertEqual(beyond["items"], [])
        self.assertFalse(beyond["has_more"])
        self.assertEqual(beyond["total"], total)

    def test_pending_tasks_only_and_agent_candidate_is_counted_once(self):
        active, _ = self.job()
        owner = self.session(status="awaiting_adoption", job_id=active)
        for status in ("queued", "running", "canceled"):
            self.job(status=status)
        for decision in ("accepted", "rejected", "partial"):
            self.job(decision=decision)
        self.job(context={**self.context, "_restored_read_only": True})
        self.job(consent={"restored_read_only": True})
        for status in ("review_ready", "canceled", "restored", "awaiting_job"):
            self.session(status=status)
        self.session(restored=True)
        result = self.inbox()
        self.assertEqual(result["counts"]["agent"], 1)
        self.assertEqual(result["counts"]["queue"], 0)
        item = next(item for item in result["items"] if item["kind"] == "agent")
        self.assertEqual(item["session_id"], owner)
        self.assertEqual(item["job_id"], active)
        self.assertFalse(item["stale"])

    def test_failed_and_unknown_jobs_need_attention_without_retrying(self):
        failed, _ = self.job(status="failed")
        unknown, _ = self.job(status="failed", run_status="unknown")
        owned, _ = self.job(status="failed")
        session = self.session(status="awaiting_job", job_id=owned)
        result = self.inbox()
        self.assertEqual(result["counts"]["queue"], 2)
        rows = {item["job_id"]: item for item in result["items"] if item.get("job_id")}
        self.assertEqual(rows[failed]["status"], "failed")
        self.assertEqual(rows[unknown]["status"], "unknown")
        self.assertEqual(rows[owned]["session_id"], session)
        self.assertEqual(rows[owned]["status"], "requires_action")

    def test_memory_wait_is_visible_and_uses_current_author_decision_sources(self):
        analysis = self.analysis()
        with closing(server.connect()) as conn, conn:
            context, chapter = server.ai_context(conn, self.chapter, task="chapter_analysis")
            context["_source"] = server.candidate_source(conn, "chapter_analysis", chapter)
            job = server.create_ai_job(conn, "chapter_analysis", context, "", "json")
            job_id = job["id"]
            conn.execute("UPDATE app_ai_job SET status='success',result_json=? WHERE id=?",
                         (server.json_text({"analysis": analysis}), job_id))
            conn.execute("UPDATE ai_run SET status='success' WHERE id=?", (job["ai_run_id"],))
        session_id = self.session(status="awaiting_memory", job_id=job_id)
        before = self.snapshot()
        first = self.inbox(kind="agent")
        self.assertEqual(first["total"], 1)
        row = first["items"][0]
        self.assertEqual(row["session_id"], session_id)
        self.assertEqual(row["status"], "awaiting_memory")
        self.assertEqual(row["status_label"], "等待确认章后资料")
        self.assertFalse(row["stale"])
        self.assertFalse(row["read_only"])
        self.assertEqual(self.snapshot(), before)
        with closing(server.connect()) as conn, conn:
            moyu_analysis.decide_analysis(conn, self.scope, analysis["id"], {
                "source_revision_id": analysis["source_revision_id"],
                "chapter_hash": analysis["chapter_hash"], "confirm_summary": True,
            })
        self.assertFalse(self.inbox(kind="agent")["items"][0]["stale"])
        with closing(server.connect()) as conn, conn:
            original = conn.execute("SELECT consent_json FROM ai_run WHERE id=?",
                                    (job["ai_run_id"],)).fetchone()[0]
            changed = json.loads(original)
            changed["request_audit"]["request_sha256"] = "0" * 64
            conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?",
                         (server.json_text(changed), job["ai_run_id"]))
        self.assertTrue(self.inbox(kind="agent")["items"][0]["stale"])
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?", (original, job["ai_run_id"]))
            server.update_chapter(conn, self.chapter, {"chapter_goal": "改由另一人物发现线索"})
        changed = self.inbox(kind="agent")["items"][0]
        self.assertTrue(changed["stale"])
        self.assertTrue(changed["read_only"])
        self.assertEqual(changed["status_label"], "来源已改变，需重新检查")

    def test_missing_analysis_cannot_appear_as_current_memory_wait(self):
        session = self.session(status="awaiting_memory")
        before = self.snapshot()
        item = self.inbox(kind="agent")["items"][0]
        self.assertEqual(item["session_id"], session)
        self.assertTrue(item["stale"])
        self.assertTrue(item["read_only"])
        self.assertEqual(self.snapshot(), before)

    def test_chat_jobs_never_become_writing_candidates_or_queue_attention(self):
        chat_jobs, writing_jobs = set(), {}
        for task in ("assistant_chat", "continue"):
            for status, run_status, expected in (
                    ("success", "success", "candidate_ready"),
                    ("failed", "failed", "failed"),
                    ("failed", "unknown", "unknown")):
                job, _ = self.job(task=task, status=status, run_status=run_status)
                if task == "assistant_chat":
                    chat_jobs.add(job)
                else:
                    writing_jobs[job] = expected
        before = self.snapshot()
        for kind in ("all", "queue"):
            with self.subTest(kind=kind):
                result = self.inbox(kind=kind)
                self.assertTrue(result["read_only"])
                self.assertEqual(result["counts"]["queue"], 3)
                rows = {item["job_id"]: item for item in result["items"]
                        if item["kind"] == "queue"}
                self.assertEqual(set(rows), set(writing_jobs))
                self.assertTrue(chat_jobs.isdisjoint(rows))
                for job, expected in writing_jobs.items():
                    self.assertEqual(rows[job]["task"], "continue")
                    self.assertEqual(rows[job]["title"], "续写候选")
                    self.assertEqual(rows[job]["status"], expected)
                    self.assertEqual(rows[job]["action"], "open_job")
                    self.assertEqual(rows[job]["read_only"], expected != "candidate_ready")
                if kind == "queue":
                    self.assertEqual(result["total"], 3)
                    self.assertFalse(result["has_more"])
                serialized = json.dumps(result, ensure_ascii=False)
                for private in (BODY, "PRIVATE_", "instruction", "context_json",
                                "consent_json", "request_audit", "provider_config"):
                    self.assertNotIn(private, serialized)
                self.assertEqual(self.snapshot(), before)

    def test_adoption_receipt_and_decision_ledger_hide_finalized_candidates(self):
        applied, run = self.job()
        decided, other_run = self.job()
        with closing(server.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO app_candidate_application(ai_run_id,project_id,branch_id,chapter_id,"
                "request_hash,source_revision_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (run, self.project, self.branch, self.chapter, "fixture", self.current["revision_id"], server.now()),
            )
            conn.execute(
                "INSERT INTO app_ai_run_decision(id,ai_run_id,project_id,branch_id,decision,created_at,actor_id) "
                "VALUES(?,?,?,?,?,?,?)",
                ("inbox_decision", other_run, self.project, self.branch, "rejected", server.now(), server.ACTOR_ID),
            )
        self.assertEqual(self.inbox(kind="queue")["total"], 0, (applied, decided))

    def test_edited_manuscript_makes_candidates_and_sessions_read_only(self):
        job, _ = self.job()
        session = self.session(status="awaiting_adoption", job_id=job)
        standalone, _ = self.job()
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": BODY + "\n她没有进门。"})
        result = self.inbox()
        for item in result["items"]:
            if item.get("session_id") == session or item.get("job_id") == standalone:
                self.assertTrue(item["stale"], item)
                self.assertTrue(item["read_only"], item)
        self.assertEqual(result["counts"]["agent"], 1)
        self.assertEqual(result["counts"]["queue"], 1)

    def test_authorization_uses_the_real_frozen_context_and_is_read_only(self):
        for mode in ("continue", "polish", "inspect"):
            with self.subTest(mode=mode):
                session = server.writing_agent.request(server, {
                    "request_id": "authorization_context_" + mode,
                    "chapter_id": self.chapter, "query": "按当前线索继续检查人物动机。", "mode": mode,
                })["session"]
                before = self.snapshot()
                with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA query_only=ON")
                    changes = conn.total_changes
                    result = moyu_inbox.writing_inbox(
                        conn, self.project, self.branch, kind="agent",
                        authorization_context=lambda chapter_id, **options: server.ai_context(
                            conn, chapter_id, **options)[0])
                    self.assertEqual(conn.total_changes, changes)
                    self.assertFalse(conn.in_transaction)
                item = next(item for item in result["items"] if item["session_id"] == session["id"])
                self.assertFalse(item["stale"])
                self.assertFalse(item["read_only"])
                self.assertNotIn("reason", item)
                self.assertEqual(self.snapshot(), before)

    def test_authorization_material_changes_do_not_look_current(self):
        session = server.writing_agent.request(server, {
            "request_id": "authorization_material_change", "chapter_id": self.chapter,
            "query": "检查本章的事实与衔接。", "mode": "inspect",
        })["session"]
        self.assertFalse(self.inbox(kind="agent")["items"][0]["stale"])
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_project_meta SET summary=? WHERE project_id=?",
                         ("核心故事背景已经改变。", self.project))
        before = self.snapshot()
        item = self.inbox(kind="agent")["items"][0]
        self.assertEqual(item["session_id"], session["id"])
        self.assertEqual(item["status"], "awaiting_authorization")
        self.assertTrue(item["stale"])
        self.assertTrue(item["read_only"])
        self.assertEqual(item["reason"], "创作资料已变化，请按当前资料重新查看计划。")
        self.assertEqual(self.snapshot(), before)

    def test_authorization_guidance_changes_without_a_new_revision_are_stale(self):
        self.session()
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_chapter_meta SET chapter_goal=? WHERE chapter_id=?",
                         ("本章目标已经改变，正文尚未修改。", self.chapter))
        item = self.inbox(kind="agent")["items"][0]
        self.assertTrue(item["stale"] and item["read_only"])
        self.assertIn("创作资料已变化", item["reason"])

    def test_missing_authorization_hash_or_reader_is_unverified(self):
        session = self.session()
        with closing(server.connect()) as conn:
            item = moyu_inbox.writing_inbox(
                conn, self.project, self.branch, kind="agent")["items"][0]
        self.assertTrue(item["stale"] and item["read_only"])
        self.assertIn("尚未核实", item["reason"])
        with closing(server.connect()) as conn, conn:
            plan = json.loads(conn.execute(
                "SELECT plan_json FROM app_agent_session WHERE id=?", (session,)).fetchone()[0])
            del plan["context_hash"]
            conn.execute("UPDATE app_agent_session SET plan_json=? WHERE id=?",
                         (server.json_text(plan), session))
        item = self.inbox(kind="agent")["items"][0]
        self.assertTrue(item["stale"] and item["read_only"])
        self.assertIn("缺少完整创作依据", item["reason"])

    def test_authorization_context_check_does_not_recheck_adoption_or_adopted_source(self):
        job, _ = self.job()
        session = self.session(status="awaiting_adoption", job_id=job)
        with closing(server.connect()) as conn, conn:
            plan = json.loads(conn.execute(
                "SELECT plan_json FROM app_agent_session WHERE id=?", (session,)).fetchone()[0])
            plan["context_hash"] = "0" * 64
            plan["adopted_source"] = {"source_revision_id": self.current["revision_id"]}
            conn.execute("UPDATE app_agent_session SET plan_json=? WHERE id=?",
                         (server.json_text(plan), session))
        with closing(server.connect()) as conn:
            with patch.object(server, "ai_context", side_effect=AssertionError("not an authorization")):
                item = moyu_inbox.writing_inbox(
                    conn, self.project, self.branch, kind="agent",
                    authorization_context=lambda chapter_id, **options: server.ai_context(
                        conn, chapter_id, **options)[0])["items"][0]
        self.assertFalse(item["stale"])
        self.assertFalse(item["read_only"])

    def test_changed_guidance_and_corrupt_frozen_context_are_stale(self):
        self.job()
        context = copy.deepcopy(self.context)
        context["current_chapter"]["title"] = "changed after the frozen request"
        corrupt, run = self.job(context=context)
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE ai_run SET input_hash='bad-hash' WHERE id=?", (run,))
            conn.execute("UPDATE app_chapter_meta SET chapter_goal='新的目标' WHERE chapter_id=?", (self.chapter,))
        rows = self.inbox(kind="queue")["items"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(item["stale"] and item["read_only"] for item in rows))
        self.assertIn(corrupt, [item["job_id"] for item in rows])

    def test_changed_consumed_material_marks_all_derived_items_stale(self):
        self.job()
        self.analysis()
        self.report()
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_project_meta SET summary='本书背景发生变化' WHERE project_id=?", (self.project,))
        result = self.inbox()
        self.assertEqual(result["counts"], {"agent": 0, "queue": 1, "analysis": 1, "review": 1})
        self.assertTrue(all(item["stale"] for item in result["items"]))

    def test_incomplete_or_canceled_success_result_is_only_readable(self):
        self.job(result={"safe_to_apply": False, "completion_state": "truncated", "truncated": True})
        canceled, _ = self.job()
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_ai_job SET cancel_requested=1 WHERE id=?", (canceled,))
        result = self.inbox(kind="queue")
        self.assertEqual(result["total"], 2)
        self.assertTrue(all(item["read_only"] and item["status"] == "read_only" for item in result["items"]))
        self.assertFalse(any(item["stale"] for item in result["items"]))

    def test_pending_analysis_is_real_and_completed_analysis_is_not_a_todo(self):
        analysis = self.analysis()
        result = self.inbox(kind="analysis")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["analysis_id"], analysis["id"])
        self.assertFalse(result["items"][0]["stale"])
        with closing(server.connect()) as conn, conn:
            moyu_analysis.decide_analysis(conn, self.scope, analysis["id"], {
                "source_revision_id": analysis["source_revision_id"], "chapter_hash": analysis["chapter_hash"],
                "summary_decision": "rejected",
            })
        self.assertEqual(self.inbox(kind="analysis")["total"], 0)

    def test_pending_analysis_becomes_read_only_after_a_manuscript_edit(self):
        self.analysis()
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": BODY + "\n新的一行。"})
        item = self.inbox(kind="analysis")["items"][0]
        self.assertEqual(item["status"], "stale")
        self.assertTrue(item["read_only"])

    def test_finalized_analysis_candidates_do_not_resurrect_after_editing(self):
        with closing(server.connect()) as conn, conn:
            context, chapter = server.ai_context(conn, self.chapter, task="chapter_analysis")
            analysis = moyu_analysis.save_analysis(
                conn, {**self.scope, "sent_context": context}, chapter, None, {
                    "summary": {"text": "", "evidence": []},
                    "handoff": {"text": "", "evidence": []},
                    "candidates": [{
                        "kind": "event", "name": "握住门把手", "note": QUOTE,
                        "source_quote": QUOTE, "epistemic_mode": "world_truth", "known_by": [],
                    }],
                })
            moyu_analysis.decide_analysis(conn, self.scope, analysis["id"], {
                "source_revision_id": analysis["source_revision_id"],
                "chapter_hash": analysis["chapter_hash"],
                "decisions": [{"candidate_id": analysis["candidates"][0]["id"], "decision": "rejected"}],
            })
            server.update_chapter(conn, self.chapter, {"body": BODY + "\n后来。"})
        self.assertEqual(self.inbox(kind="analysis")["total"], 0)

    def test_independent_memory_has_distinct_navigation_and_stale_evidence(self):
        with closing(server.connect()) as conn, conn:
            span = server.source_span_for_memory(conn, {
                "source_chapter_id": self.chapter, "source_quote": QUOTE,
            })
            for index, source_span in enumerate((span, None)):
                conn.execute(
                    "INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,kind,payload_json,"
                    "status,confidence,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (f"inbox_memory_{index}", self.project, self.branch, source_span, "人物",
                     server.json_text({"source_chapter_id": self.chapter, "name": "PRIVATE_MEMORY_TEXT"}),
                     "candidate", 0.5, server.now(), server.ACTOR_ID),
                )
        rows = {item["memory_id"]: item for item in self.inbox(kind="analysis")["items"]}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["inbox_memory_0"]["action"], "open_memory")
        self.assertFalse(rows["inbox_memory_0"]["stale"])
        self.assertTrue(rows["inbox_memory_1"]["stale"])
        self.assertTrue(rows["inbox_memory_1"]["read_only"])

    def test_independent_memory_payload_bindings_cannot_hide_behind_a_valid_span(self):
        _, payload = self.independent_memory()
        for change in (
                {"source_chapter_id": "other_chapter"}, {"source_revision_id": "old_revision"},
                {"source_quote": "这句并不存在。"}, {"source_hash": "bad-hash"},
                {"source_span_id": "different_span"}, {"chapter_hash": "bad-manuscript-hash"},
                {"source_start": 1}, {"source_end": len(QUOTE) - 1}):
            with self.subTest(change=change):
                with closing(server.connect()) as conn, conn:
                    conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id=?",
                                 (server.json_text({**payload, **change}), "inbox_bound_memory"))
                before = self.snapshot()
                row = self.inbox(kind="analysis")["items"][0]
                self.assertEqual(row["memory_id"], "inbox_bound_memory")
                self.assertEqual(row["status"], "stale")
                self.assertTrue(row["stale"] and row["read_only"])
                self.assertEqual(self.snapshot(), before)

    def test_independent_memory_saved_utf8_ranges_are_verified(self):
        span, _ = self.independent_memory()
        for start, end in ((1, len(QUOTE.encode("utf-8"))), (0, len(QUOTE.encode("utf-8")) - 1)):
            with self.subTest(start=start, end=end):
                with closing(server.connect()) as conn, conn:
                    conn.execute("UPDATE source_span SET start_utf8=?,end_utf8=? WHERE id=?",
                                 (start, end, span))
                before = self.snapshot()
                row = self.inbox(kind="analysis")["items"][0]
                self.assertTrue(row["stale"] and row["read_only"])
                self.assertEqual(self.snapshot(), before)

    def test_independent_memory_checks_each_declared_offset_without_coercing_numbers(self):
        _, payload = self.independent_memory()
        declared = {
            **payload, "source_start": "0", "source_end": str(len(QUOTE)),
            "source_start_codepoint": "0", "source_end_codepoint": str(len(QUOTE)),
            "source_start_utf8": "0", "source_end_utf8": str(len(QUOTE.encode("utf-8"))),
        }
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id=?",
                         (server.json_text(declared), "inbox_bound_memory"))
        current = self.inbox(kind="analysis")["items"][0]
        self.assertFalse(current["stale"] or current["read_only"])
        for change in (
                {"source_start": 1}, {"source_start_codepoint": 1},
                {"source_end_codepoint": len(QUOTE) - 1},
                {"source_start": False}, {"source_start": 0.0},
                {"source_start_codepoint": False}, {"source_start_codepoint": 0.0},
                {"source_start_utf8": 1}, {"source_end_utf8": len(QUOTE.encode("utf-8")) - 1}):
            with self.subTest(change=change):
                with closing(server.connect()) as conn, conn:
                    conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id=?",
                                 (server.json_text({**declared, **change}), "inbox_bound_memory"))
                before = self.snapshot()
                row = self.inbox(kind="analysis")["items"][0]
                self.assertTrue(row["stale"] and row["read_only"])
                self.assertEqual(self.snapshot(), before)

    def test_independent_memory_cannot_move_to_a_repeated_quote(self):
        body = BODY + "\n" + QUOTE
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": body})
        span, _ = self.independent_memory()
        self.assertFalse(self.inbox(kind="analysis")["items"][0]["stale"])
        second = body.rindex(QUOTE)
        with closing(server.connect()) as conn, conn:
            conn.execute(
                "UPDATE source_span SET start_codepoint=?,end_codepoint=?,start_utf8=?,end_utf8=? WHERE id=?",
                (second, second + len(QUOTE), len(body[:second].encode("utf-8")),
                 len(body[:second + len(QUOTE)].encode("utf-8")), span))
        before = self.snapshot()
        row = self.inbox(kind="analysis")["items"][0]
        self.assertTrue(row["stale"] and row["read_only"])
        self.assertEqual(self.snapshot(), before)

    def test_review_needs_revision_is_not_finished_and_resolutions_follow_gate(self):
        report = self.report()
        self.decide_report(report, "accepted")
        result = self.inbox(kind="review")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["status"], "needs_revision")
        self.assertEqual(result["items"][0]["pending_count"], 1)
        self.decide_report(report, "explained")
        self.assertEqual(self.inbox(kind="review")["total"], 0)

    def test_superseded_reviews_are_not_double_counted_per_chapter(self):
        first = self.report(message="第一份合成问题")
        second = self.report(message="第二份合成问题")
        result = self.inbox(kind="review")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["review_ids"], [second["id"]])
        self.assertNotEqual(first["id"], second["id"])

    def test_accepted_current_revision_issue_survives_a_new_review(self):
        first = self.report()
        self.decide_report(first, "accepted")
        second = self.report(message="另一份问题")
        result = self.inbox(kind="review")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["status"], "needs_revision")
        self.assertCountEqual(result["items"][0]["review_ids"], [first["id"], second["id"]])
        self.assertEqual(result["items"][0]["pending_count"], 2)

    def test_missing_and_empty_reviews_never_become_a_pass(self):
        first = self.inbox(kind="review")
        self.assertEqual(first["items"][0]["status"], "unreviewed")
        self.report(empty=True)
        second = self.inbox(kind="review")
        self.assertEqual(second["total"], 1)
        self.assertEqual(second["items"][0]["status"], "no_findings_unverified")
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": BODY + "\n改稿。"})
        self.assertEqual(self.inbox(kind="review")["items"][0]["status"], "stale")

    def test_stale_review_and_current_explicit_author_resolution_match_gate(self):
        report = self.report()
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": BODY + "\n作者修订。"})
        self.assertEqual(self.inbox(kind="review")["items"][0]["status"], "stale")
        self.decide_report(report, "explained")
        with closing(server.connect()) as conn:
            self.assertFalse(moyu_analysis.review_publication_gate(conn, self.scope)["issues"])
        self.assertEqual(self.inbox(kind="review")["total"], 0)

    def test_project_and_branch_scope_do_not_leak(self):
        original = self.inbox()
        with closing(server.connect()) as conn, conn:
            other = server.new_project(conn, {"title": "另一本合成作品"})
            other_project, other_branch = other["project"]["id"], other["project"]["branch_id"]
        with moyu_workspace.use_scope(other_project, other_branch, "fixture"):
            with closing(server.connect()) as conn, conn:
                server.ensure_seed(conn)
                other_chapter = server.chapter_rows(conn)[0]["id"]
            saved = self.project, self.branch, self.chapter, self.context, self.current
            self.project, self.branch, self.chapter = other_project, other_branch, other_chapter
            with closing(server.connect()) as conn:
                self.context, self.current = server.ai_context(conn, self.chapter, task="continue")
                self.context["_source"] = server.candidate_source(conn, "continue", self.current)
            self.job()
            self.session()
            self.project, self.branch, self.chapter, self.context, self.current = saved
        self.assertEqual(self.inbox(), original)
        with closing(server.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO branch(id,project_id,status,acl_json) VALUES(?,?,?,?)",
                ("inbox_other_branch", self.project, "active", "{}"),
            )
            isolated = moyu_inbox.writing_inbox(conn, self.project, "inbox_other_branch")
            self.assertEqual(isolated["total"], 0)
            with self.assertRaises(LookupError):
                moyu_inbox.writing_inbox(conn, self.project, other_branch)
            with self.assertRaises(LookupError):
                moyu_inbox.writing_inbox(conn, "inbox_missing_project", self.branch)

    def test_planning_authorization_confirmation_and_application_are_waiting(self):
        statuses = ("awaiting_authorization", "awaiting_plan_confirmation", "awaiting_plan_application")
        sessions = [self.session(status=status) for status in statuses]
        with closing(server.connect()) as conn, conn:
            current = moyu_workspace.blueprint_row(conn, self.project, self.branch)
            source = {"blueprint_version": current["version"], "blueprint_hash": server.planning_hash({
                "version": current["version"], "blueprint": current["blueprint"],
            })}
            for session in sessions:
                raw = json.loads(conn.execute(
                    "SELECT plan_json FROM app_agent_session WHERE id=?", (session,),
                ).fetchone()[0])
                raw.update(mode="plan", source=source)
                conn.execute("UPDATE app_agent_session SET plan_json=? WHERE id=?",
                             (server.json_text(raw), session))
        result = self.inbox(kind="agent")
        self.assertEqual(result["total"], 3)
        self.assertCountEqual([item["status"] for item in result["items"]], statuses)
        self.assertTrue(all(not item["stale"] for item in result["items"]))
        with closing(server.connect()) as conn, conn:
            moyu_workspace.put_blueprint(conn, self.project, self.branch, {
                "expected_version": current["version"], "blueprint": {"premise": "新的故事总纲"},
            })
        changed = self.inbox(kind="agent")
        self.assertTrue(all(item["stale"] and item["read_only"] for item in changed["items"]))

    def test_parameter_errors_never_write_or_fall_back_to_zero(self):
        before = self.snapshot()
        invalid = (
            {"limit": True}, {"limit": 0}, {"limit": 101}, {"limit": "25"},
            {"offset": True}, {"offset": -1}, {"offset": 2_147_483_648},
            {"kind": []}, {"kind": None}, {"kind": "unknown"},
        )
        for parameters in invalid:
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.inbox(**parameters)
        self.assertEqual(self.snapshot(), before)

    def test_database_read_errors_and_corrupt_records_are_not_empty_success(self):
        self.session()
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_agent_session SET plan_json='broken-json'")
        with self.assertRaises(ValueError):
            self.inbox()
        with closing(server.connect()) as conn, conn:
            conn.execute("DROP INDEX app_story_review_scope")
        before = self.snapshot()
        with self.assertRaisesRegex(sqlite3.OperationalError, "待办暂不可用"):
            self.inbox()
        self.assertEqual(self.snapshot(), before)

    def test_failed_select_is_not_reported_as_an_empty_inbox(self):
        with closing(server.connect()) as conn:
            def deny_agent_read(action, table, *_rest):
                return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "app_agent_session" else sqlite3.SQLITE_OK
            conn.set_authorizer(deny_agent_read)
            with self.assertRaises(sqlite3.DatabaseError):
                moyu_inbox.writing_inbox(conn, self.project, self.branch)
            conn.set_authorizer(None)
            self.assertFalse(conn.in_transaction)

    def test_read_only_connection_preserves_all_tables_and_excludes_private_content(self):
        self.job()
        self.session()
        self.analysis()
        self.report()
        before = self.snapshot()
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            changes = conn.total_changes
            result = moyu_inbox.writing_inbox(conn, self.project, self.branch)
            self.assertEqual(conn.total_changes, changes)
            self.assertFalse(conn.in_transaction)
        serialized = json.dumps(result, ensure_ascii=False)
        for private in (BODY, "PRIVATE_", "context_json", "consent_json", "request_audit",
                        "provider_config", "source_body_hash", "input_hash"):
            self.assertNotIn(private, serialized)
        self.assertEqual(self.snapshot(), before)

    def test_caller_transaction_is_preserved_on_success_and_failure(self):
        with closing(server.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE app_project_meta SET summary='caller-uncommitted' WHERE project_id=?", (self.project,))
            moyu_inbox.writing_inbox(conn, self.project, self.branch)
            self.assertTrue(conn.in_transaction)
            conn.execute("DROP INDEX app_story_review_scope")
            with self.assertRaises(sqlite3.OperationalError):
                moyu_inbox.writing_inbox(conn, self.project, self.branch)
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.execute(
                "SELECT summary FROM app_project_meta WHERE project_id=?", (self.project,),
            ).fetchone()[0], "caller-uncommitted")
            conn.rollback()
        self.inbox()

    def test_one_snapshot_survives_a_concurrent_queue_insert(self):
        self.job()
        with closing(server.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        original = moyu_inbox._agents
        invoked = []

        def insert_after_initial_reads(*args, **kwargs):
            if not invoked:
                invoked.append(True)
                self.job()
                with closing(server.connect()) as writer, writer:
                    server.update_chapter(writer, self.chapter, {"body": BODY + "\n另一窗口刚刚改稿。"})
                self.report()
            return original(*args, **kwargs)

        with patch.object(moyu_inbox, "_agents", side_effect=insert_after_initial_reads):
            first = self.inbox()
        self.assertEqual(first["counts"]["queue"], 1)
        self.assertEqual(first["total"], 2)
        self.assertFalse(next(item for item in first["items"] if item["kind"] == "queue")["stale"])
        self.assertEqual(next(item for item in first["items"] if item["kind"] == "review")["status"], "unreviewed")
        second = self.inbox()
        self.assertEqual(second["counts"]["queue"], 2)
        self.assertEqual(second["total"], 3)
        self.assertTrue(all(item["stale"] for item in second["items"] if item["kind"] == "queue"))
        self.assertEqual(next(item for item in second["items"] if item["kind"] == "review")["status"], "pending_author_review")


if __name__ == "__main__":
    unittest.main(verbosity=2)
