#!/usr/bin/env python3
"""Read-only HTTP evidence checks against isolated books and no model calls."""
from __future__ import annotations

from contextlib import closing
from http.server import ThreadingHTTPServer
import json
import os
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
import moyu_workspace
import server


QUOTE = "林岚把🗝️铜钥匙放回门边。"
BODY = QUOTE + "\n门里传来两下轻响。\n" + QUOTE


class QuietHandler(server.Handler):
    def log_message(self, *_arguments):
        pass


class MemoryCandidateEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="moyu-memory-evidence-")
        cls.previous_path = server.DB_PATH
        cls.environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_SECRET_KEY": "", "MOYU_CREDENTIALS_DIR": str(Path(cls.temporary.name) / "keys"),
        })
        cls.environment.start()
        cls.blockers = [patch.object(server, name, side_effect=AssertionError("model dispatch forbidden"))
                        for name in ("call_ai_provider", "start_ai_worker")]
        cls.mocks = [blocker.start() for blocker in cls.blockers]
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"
        cls.sequence = 0

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=5)
        for mock in cls.mocks:
            mock.assert_not_called()
        for blocker in cls.blockers:
            blocker.stop()
        cls.environment.stop()
        server.DB_PATH = cls.previous_path
        cls.temporary.cleanup()

    def call(self, method, route, payload=None, *, project=None, branch=None):
        headers = {"Content-Type": "application/json", "X-Moyu-Project": project or self.project}
        if branch:
            headers["X-Moyu-Branch"] = branch
        request = urllib.request.Request(
            self.base + route, method=method, headers=headers,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode())
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def snapshot(self):
        with closing(server.connect()) as conn:
            return tuple(conn.iterdump()), conn.execute("PRAGMA user_version").fetchone()[0]

    def read(self, route, *, project=None, branch=None, status=200):
        before = self.snapshot()
        code, data = self.call("GET", route, project=project, branch=branch)
        self.assertEqual(self.snapshot(), before, "reading evidence must not write the database")
        self.assertEqual(code, status, data)
        return data

    def setUp(self):
        type(self).sequence += 1
        server.DB_PATH = Path(self.temporary.name) / f"case-{self.sequence}.sqlite3"
        server.init_db()
        self.project, self.branch = server.PROJECT_ID, server.BRANCH_ID
        code, state = self.call("GET", "/api/state")
        self.assertEqual(code, 200)
        self.chapter = state["chapters"][0]["id"]
        state["chapters"][0].update(body=BODY, pov="林岚")
        state["records"] = [{"id": "evidence_lan", "name": "林岚", "type": "人物",
                             "description": "独立合成记忆的关联人物。"}]
        self.assertEqual(self.call("PUT", "/api/state", state)[0], 200)
        self.memory = self.create_memory("candidate")
        with closing(server.connect()) as conn:
            self.span = conn.execute(
                "SELECT source_span_id FROM candidate_memory WHERE id=?", (self.memory,)).fetchone()[0]

    def create_memory(self, status, **source):
        code, data = self.call("POST", "/api/memory", {
            "type": "人物", "name": "林岚", "status": status,
            "source_chapter_id": self.chapter, "source_quote": QUOTE,
            "source_start": 0, "source_end": len(QUOTE), **source,
        })
        self.assertEqual(code, 201, data)
        return data["id"]

    def evidence(self, memory=None):
        identifier = memory or self.memory
        return [self.read(f"/api/memory/{identifier}" + suffix)
                for suffix in ("", "/evidence")]

    def assert_stale(self, *, memory=None, reason=None):
        for data in self.evidence(memory):
            rows = [row for row in data["evidence"]
                    if row.get("candidate_memory_id") == (memory or self.memory)]
            self.assertTrue(rows, data)
            self.assertTrue(all(row["stale"] for row in rows), rows)
            self.assertTrue(all(not row.get("source_span") or not row["source_span"]["current"]
                                for row in rows), rows)
            if reason:
                self.assertTrue(any(reason in row["stale_reasons"] for row in rows), rows)

    def test_candidate_unicode_source_does_not_become_canon(self):
        for data in self.evidence():
            self.assertEqual(data["assertions"], [])
            self.assertEqual(len(data["evidence"]), 1)
            row = data["evidence"][0]
            self.assertEqual(row["candidate_memory"]["status"], "candidate")
            self.assertIsNone(row["candidate_memory"]["reviewed_at"])
            self.assertFalse(row["stale"])
            self.assertTrue(row["source_span"]["current"])
            self.assertEqual(row["source_span"]["quote"], QUOTE)
            self.assertEqual(row["source_span"]["start_codepoint"], 0)
            self.assertEqual(row["source_span"]["end_codepoint"], len(QUOTE))
            self.assertEqual(row["source_span"]["quote_hash"], server.hash_text(QUOTE))

    def test_existing_assertion_evidence_is_not_duplicated(self):
        accepted = self.create_memory("accepted")
        for data in self.evidence(accepted):
            self.assertEqual(len(data["assertions"]), 1)
            self.assertEqual(data["assertions"][0]["state"], "canon")
            self.assertEqual(len(data["evidence"]), 1)
            self.assertIn("assertion_id", data["evidence"][0])
            self.assertEqual(data["evidence"][0]["candidate_memory"]["status"], "accepted")

    def test_old_revision_and_invalid_span_status_are_stale(self):
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": BODY + "\n新的一行。"})
        self.assert_stale(reason="source_revision_outdated")

    def test_missing_source_and_explicit_stale_status_are_readable(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE source_span SET status='stale' WHERE id=?", (self.span,))
        self.assert_stale(reason="source_span_stale")
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE candidate_memory SET source_span_id=NULL WHERE id=?", (self.memory,))
        self.assert_stale(reason="source_span_missing")

    def test_deleted_chapter_never_has_current_source(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO app_deleted_node VALUES(?,?,?)",
                         (self.chapter, server.now(), "synthetic deleted source"))
        self.assert_stale(reason="source_chapter_deleted")

    def test_direct_quote_and_hash_tampering_are_stale(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE source_span SET quote=? WHERE id=?", ("伪造的原句。", self.span))
        self.assert_stale(reason="invalid_source_quote")
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE source_span SET quote=?,quote_hash='bad-hash' WHERE id=?",
                         (QUOTE, self.span))
        self.assert_stale(reason="invalid_source_quote")

    def test_payload_chapter_and_quote_must_agree_with_source(self):
        with closing(server.connect()) as conn, conn:
            raw = json.loads(conn.execute(
                "SELECT payload_json FROM candidate_memory WHERE id=?", (self.memory,)).fetchone()[0])
            raw["source_quote"] = "原句已被替换。"
            conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id=?",
                         (server.json_text(raw), self.memory))
        self.assert_stale(reason="invalid_source_quote")

    def test_assertion_does_not_bypass_quote_tamper_validation(self):
        accepted = self.create_memory("accepted")
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE source_span SET quote=?,quote_hash=? WHERE id=?",
                         ("被篡改但重新计算了指纹。", server.hash_text("被篡改但重新计算了指纹。"), self.span))
        self.assert_stale(memory=accepted, reason="invalid_source_quote")

    def test_repeated_quote_keeps_the_exact_payload_range(self):
        with closing(server.connect()) as conn, conn:
            second = BODY.rindex(QUOTE)
            conn.execute("UPDATE source_span SET start_codepoint=?,end_codepoint=? WHERE id=?",
                         (second, second + len(QUOTE), self.span))
        self.assert_stale(reason="invalid_source_quote")

    def test_foreign_memory_lookup_does_not_seed_another_book_or_branch(self):
        code, other = self.call("POST", "/api/projects", {"title": "另一本没有设定的合成作品"})
        self.assertEqual(code, 201, other)
        for suffix in ("", "/evidence"):
            self.read(f"/api/memory/{self.memory}" + suffix, project=other["project"]["id"], status=404)
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO branch(id,project_id,status,acl_json) VALUES(?,?,?,?)",
                         ("evidence_empty_branch", self.project, "active", "{}"))
        for suffix in ("", "/evidence"):
            self.read(f"/api/memory/{self.memory}" + suffix, branch="evidence_empty_branch", status=404)

    def test_cross_book_or_branch_source_assignment_is_rejected(self):
        code, other = self.call("POST", "/api/projects", {"title": "另一来源作品"})
        self.assertEqual(code, 201, other)
        other_id, other_branch = other["project"]["id"], other["project"]["branch_id"]
        with moyu_workspace.use_scope(other_id, other_branch, "fixture"):
            with closing(server.connect()) as conn, conn:
                server.ensure_seed(conn)
                other_chapter = server.chapter_rows(conn)[0]["id"]
                server.update_chapter(conn, other_chapter, {"body": "禁止泄漏的另一作品原句。"})
                foreign_span = server.source_span_for_memory(conn, {
                    "source_chapter_id": other_chapter, "source_quote": "禁止泄漏的另一作品原句。",
                })
        before = self.snapshot()
        with closing(server.connect()) as conn, conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "candidate scope mismatch"):
                conn.execute("UPDATE candidate_memory SET source_span_id=? WHERE id=?",
                             (foreign_span, self.memory))
        self.assertEqual(self.snapshot(), before)
        for data in self.evidence():
            row = data["evidence"][0]
            self.assertFalse(row["stale"])
            self.assertEqual(row["source_span"]["id"], self.span)
            self.assertNotIn("禁止泄漏的另一作品原句", json.dumps(data, ensure_ascii=False))
        with closing(server.connect()) as conn, conn:
            conn.execute("INSERT INTO branch(id,project_id,status,acl_json) VALUES(?,?,?,?)",
                         ("evidence_source_branch", self.project, "active", "{}"))
        with moyu_workspace.use_scope(self.project, "evidence_source_branch", server.ACTOR_ID):
            with closing(server.connect()) as conn, conn:
                foreign_chapter = server.create_chapter(
                    conn, {"title": "另一分支", "body": "禁止泄漏的另一分支原句。"})
                foreign_span = server.source_span_for_memory(conn, {
                    "source_chapter_id": foreign_chapter, "source_quote": "禁止泄漏的另一分支原句。",
                })
        before = self.snapshot()
        with closing(server.connect()) as conn, conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "candidate scope mismatch"):
                conn.execute("UPDATE candidate_memory SET source_span_id=? WHERE id=?",
                             (foreign_span, self.memory))
        self.assertEqual(self.snapshot(), before)
        for data in self.evidence():
            self.assertFalse(data["evidence"][0]["stale"])
            self.assertEqual(data["evidence"][0]["source_span"]["id"], self.span)
            self.assertNotIn("禁止泄漏的另一分支原句", json.dumps(data, ensure_ascii=False))

    def test_job_projection_exposes_execution_status_without_private_or_cross_scope_leak(self):
        with closing(server.connect()) as conn, conn:
            context, chapter = server.ai_context(conn, self.chapter, task="continue")
            context["_source"] = server.candidate_source(conn, "continue", chapter)
            run_id, job_id = "evidence_unknown_run", "evidence_unknown_job"
            conn.execute(
                "INSERT INTO ai_run(id,project_id,branch_id,mode,provider,model,system_prompt_hash,"
                "prompt_hash,input_hash,started_at,status,author_decision,consent_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, self.project, self.branch, "fixture", "local", "synthetic", "fixed", "fixed",
                 server.hash_text(server.json_text(context)), server.now(), "unknown", "pending", "{}"))
            conn.execute(
                "INSERT INTO app_ai_job(id,project_id,branch_id,ai_run_id,task,chapter_id,instruction,"
                "context_json,response_format,max_tokens,status,result_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, self.project, self.branch, run_id, "continue", self.chapter,
                 "PRIVATE_INSTRUCTION", server.json_text(context), "text", 100, "failed",
                 server.json_text({"completion_state": "unknown", "billing_state": "unknown"}),
                 server.now(), server.now()))
        job = self.read("/api/ai/jobs/" + job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["execution_status"], "unknown")
        self.assertEqual(job["result"]["billing_state"], "unknown")
        self.assertNotIn("PRIVATE_INSTRUCTION", json.dumps(job))
        for private_field in ("run_status", "retry_consent", "context", "context_json", "instruction"):
            self.assertNotIn(private_field, job)
        queued = self.read("/api/ai/queue")
        public_job = next(row for row in queued["jobs"] if row["id"] == job_id)
        self.assertEqual(public_job["execution_status"], "unknown")
        for private_field in ("run_status", "retry_consent", "context", "context_json", "instruction"):
            self.assertNotIn(private_field, public_job)
        code, other = self.call("POST", "/api/projects", {"title": "其他账本作品"})
        self.assertEqual(code, 201, other)
        self.read("/api/ai/jobs/" + job_id, project=other["project"]["id"], status=404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
