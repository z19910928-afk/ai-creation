#!/usr/bin/env python3
"""Author chat through real HTTP routes, temporary SQLite and a free local model."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_agent_chat as chat
import moyu_backups as backups
import moyu_settings as settings
import moyu_workspace as workspace
import server


MANUSCRIPT = "林岚握住旧门的把手。\n门里传来两下轻响。"
PRIVATE_KEY = "synthetic-local-chat-provider-private-key"
MODEL = "local-chat-fixture"
REPORT_SCOPE = (
    "real application HTTP, isolated temporary SQLite and local HTTP provider; "
    "no production data, real credentials or paid provider calls"
)
SOURCE_FILES = (
    "server.py", "moyu_agent_chat.py", "moyu_agent_service.py", "moyu_agent_settings.py",
    "moyu_backups.py", "moyu_settings.py", "moyu_provider.py", "moyu_materials.py",
    "tests/agent_chat_fixtures.py",
)


def source_fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES}


class Provider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls: list[dict] = []
    database: Path | None = None
    started = threading.Event()
    release = threading.Event()

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        case = self.path.split("/")[1]
        request_id = self.headers.get("X-Client-Request-Id")
        with sqlite3.connect(self.database) as conn:
            ledger = conn.execute(
                "SELECT q.status,j.status,a.consent_json FROM ai_run a "
                "JOIN app_quota_reservation q ON q.ai_run_id=a.id "
                "JOIN app_ai_job j ON j.ai_run_id=a.id WHERE a.id=?", (request_id,),
            ).fetchone()
        self.calls.append({
            "case": case, "payload": payload, "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "request_id": request_id,
            "ledger": (ledger[0], ledger[1], json.loads(ledger[2])) if ledger else None,
        })
        actions = []
        if case in {"actions", "held-actions"}:
            actions = [
                {"kind": kind, "label": label, "instruction": "检查门内声音并保持林岚视角。"}
                for kind, label in (("continue", "接着写"), ("inspect", "检查本章"),
                                    ("plan", "规划后续"))
            ] + [{"kind": "open", "label": "查看记忆", "instruction": "", "view": "memory"}]
        elif case == "polish":
            actions = [{"kind": "polish", "label": "修订选区", "instruction": "保留动作，压缩修辞。",
                        "reference_index": 0}]
        elif case == "reference-only":
            actions = [
                {"kind": "continue", "label": "续写", "instruction": "继续整章。"},
                {"kind": "polish", "label": "缺失引用", "instruction": "修改整章。"},
                {"kind": "polish", "label": "修订原句", "instruction": "保留事实。",
                 "reference_index": 0},
                {"kind": "open", "label": "查看原稿", "instruction": "", "view": "editor"},
            ]
        elif case == "bad-action":
            actions = [{"kind": "delete", "label": "删除", "instruction": "删除原稿。"}]
        reply = json.dumps({"reply": "门内的两下轻响提供了可追踪线索，应先保留不确定性。",
                            "actions": actions}, ensure_ascii=False)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            midpoint = len(reply) // 2
            self.event({"model": payload["model"], "choices": [
                {"index": 0, "delta": {"content": reply[:midpoint]}, "finish_reason": None}]})
            if case in {"held", "held-actions"}:
                self.started.set()
                self.release.wait(timeout=8)
            if case == "eof":
                return
            self.event({"choices": [{"index": 0, "delta": {"content": reply[midpoint:]},
                                      "finish_reason": "stop"}]})
            self.event({"choices": [], "usage": {
                "prompt_tokens": 21, "completion_tokens": 13, "total_tokens": 34}})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def event(self, value):
        self.wfile.write(("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode())
        self.wfile.flush()

    def log_message(self, *_args):
        pass


class ChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        cls.provider.daemon_threads = True
        cls.provider_thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.provider_thread.start()
        cls.base = f"http://127.0.0.1:{cls.provider.server_port}"
        cls.total_local_calls = 0

    @classmethod
    def tearDownClass(cls):
        Provider.release.set()
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.provider_thread.join(timeout=3)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="moyu-chat-http-fixture-")
        self.root = Path(self.temporary.name)
        self.previous_db = server.DB_PATH
        self.environment = patch.dict(os.environ, {
            "MOYU_CREDENTIALS_DIR": str(self.root / "private-credentials"),
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_AI_DAILY_QUOTA": "100",
        })
        self.environment.start()
        server.DB_PATH = self.root / "book.sqlite3"
        self.worker_patch = patch.object(server, "start_ai_worker", return_value=False)
        self.worker = self.worker_patch.start()
        self.jobs: list[threading.Thread] = []
        Provider.database = server.DB_PATH
        Provider.calls.clear()
        Provider.started.clear()
        Provider.release.clear()
        server.init_db()
        self.project, self.branch = server.PROJECT_ID, server.BRANCH_ID
        with closing(server.connect()) as conn, conn:
            server.ensure_seed(conn)
            self.chapter = server.chapter_rows(conn)[0]["id"]
            server.update_chapter(conn, self.chapter, {"body": MANUSCRIPT})

        class QuietHandler(server.Handler):
            def log_message(self, *_args):
                pass

        self.app = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.app.daemon_threads = True
        self.app_thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.app_thread.start()

    def tearDown(self):
        Provider.release.set()
        for job in self.jobs:
            job.join(timeout=10)
        self.app.shutdown()
        self.app.server_close()
        self.app_thread.join(timeout=3)
        self.worker_patch.stop()
        server.DB_PATH = self.previous_db
        self.environment.stop()
        self.temporary.cleanup()
        type(self).total_local_calls += len(Provider.calls)

    def request(self, method, path, body=None, *, project=None, branch=None):
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        with closing(http.client.HTTPConnection(*self.app.server_address, timeout=10)) as conn:
            conn.request(method, path, body=raw, headers={
                "Content-Type": "application/json",
                "X-Moyu-Project": project or self.project,
                "X-Moyu-Branch": branch or self.branch,
            })
            response = conn.getresponse()
            return response.status, json.loads(response.read())

    def configure(self, case="normal"):
        with closing(server.connect()) as conn, conn:
            settings.save_settings(conn, self.project, {
                "base_url": self.base + "/" + case, "model": MODEL,
                "api_key": PRIVATE_KEY, "daily_limit": 100,
                "parameters": {"temperature": 0, "max_tokens": 3000},
            })

    def create(self, request_id="chat_session_request"):
        status, result = self.request("POST", "/api/agent-chat/sessions",
                                      {"request_id": request_id})
        self.assertEqual(status, 201, result)
        return result

    def send(self, session, request_id="chat_message_request", **overrides):
        return self.request("POST", f"/api/agent-chat/sessions/{session['id']}/messages", {
            "request_id": request_id, "content": "请分析门内的声音，先讨论再给具体创作建议。",
            "chapter_id": self.chapter, **overrides,
        })

    def process(self, queued):
        job = server.process_ai_job(queued["active_run"]["id"])
        return job, self.read(queued["session"])

    def read(self, session):
        status, result = self.request("GET", f"/api/agent-chat/sessions/{session['id']}")
        self.assertEqual(status, 200, result)
        return result

    def complete(self, case="normal", **overrides):
        self.configure(case)
        session = self.create()["session"]
        status, queued = self.send(session, **overrides)
        self.assertEqual(status, 202, queued)
        job, result = self.process(queued)
        self.assertEqual(job["status"], "success", job)
        return result

    def counts(self):
        tables = ("app_agent_chat_session", "app_agent_chat_turn", "app_agent_chat_action",
                  "app_agent_session", "app_ai_job", "ai_run", "app_quota_reservation")
        with closing(server.connect()) as conn:
            return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in tables)

    def snapshot(self):
        with closing(server.connect()) as conn:
            return tuple(conn.iterdump())

    def reference(self, start=0, end=10):
        with closing(server.connect()) as conn:
            chapter = next(c for c in server.chapter_rows(conn) if c["id"] == self.chapter)
            return server.candidate_source(conn, "polish", chapter, {
                "range": {"start": start, "end": end, "quote": chapter["body"][start:end]}})

    def provider_material(self):
        content = Provider.calls[-1]["payload"]["messages"][-1]["content"]
        return json.loads(content.split("<manuscript>\n", 1)[1].split("\n</manuscript>", 1)[0])

    def confirm(self, action):
        return self.request("POST", f"/api/agent-chat/actions/{action['id']}/confirm",
                            {"confirm": True})

    def test_missing_connection_creates_no_job_or_quota_and_no_offline_reply(self):
        session = self.create()["session"]
        before = self.snapshot()
        status, result = self.send(session)
        self.assertEqual(status, 400, result)
        self.assertIn("配置", result["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.read(session)["messages"], [])
        self.assertEqual(self.counts()[4:], (0, 0, 0))
        self.worker.assert_not_called()
        self.assertEqual(Provider.calls, [])

    def test_session_creation_and_message_submission_are_idempotent(self):
        self.configure()
        session = self.create()["session"]
        repeated = self.create()
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["session"]["id"], session["id"])
        status, queued = self.send(session)
        self.assertEqual(status, 202, queued)
        before = self.counts()
        status, repeated = self.send(session)
        self.assertEqual(status, 202, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["active_run"]["id"], queued["active_run"]["id"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.worker.call_count, 1)
        status, conflict = self.send(session, content="换成另一条消息。")
        self.assertEqual(status, 400, conflict)
        self.assertIn("内容已变化", conflict["error"])
        self.assertEqual(self.counts(), before)
        self.process(queued)
        status, repeated = self.send(session)
        self.assertEqual(status, 202, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(Provider.calls), 1)

    def test_concurrent_duplicate_messages_create_one_metered_job(self):
        self.configure()
        session = self.create()["session"]
        gate, outcomes = threading.Barrier(3), []

        def submit():
            gate.wait(timeout=5)
            outcomes.append(self.send(session))

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        gate.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(status == 202 for status, _ in outcomes), outcomes)
        self.assertEqual(len({result["active_run"]["id"] for _, result in outcomes}), 1)
        self.assertEqual(sorted(result["idempotent"] for _, result in outcomes), [False, True])
        self.assertEqual(self.counts(), (1, 1, 0, 0, 1, 1, 1))
        self.assertEqual(self.worker.call_count, 1)

    def test_inflight_session_rejects_a_second_distinct_message(self):
        self.configure()
        session = self.create()["session"]
        status, queued = self.send(session)
        self.assertEqual(status, 202, queued)
        before = self.counts()
        status, rejected = self.send(session, request_id="second_message")
        self.assertEqual(status, 400, rejected)
        self.assertIn("上一条", rejected["error"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(Provider.calls, [])

    def test_invalid_message_inputs_do_not_write_or_dispatch(self):
        self.configure()
        session = self.create()["session"]
        for change in (
            {"content": ""}, {"content": []}, {"content": "x" * 6001},
            {"include_chapter": "yes"}, {"references": {}}, {"references": [1]},
            {"references": [self.reference()] * 4}, {"unsupported": True},
            {"request_id": "contains space"},
        ):
            with self.subTest(change=change):
                before = self.snapshot()
                status, result = self.send(session, **change)
                self.assertEqual(status, 400, result)
                self.assertEqual(self.snapshot(), before)
        self.worker.assert_not_called()
        self.assertEqual(Provider.calls, [])

    def test_forged_reference_quote_returns_conflict_before_task_creation(self):
        self.configure()
        session = self.create()["session"]
        source = self.reference()
        source["range"]["quote"] = "伪造的旧稿原句"
        before = self.snapshot()
        status, rejected = self.send(session, references=[source])
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.snapshot(), before)
        self.worker.assert_not_called()
        self.assertEqual(Provider.calls, [])

    def test_real_stream_reply_preserves_prose_and_dispatch_audit(self):
        before = self.reference(end=len(MANUSCRIPT))
        result = self.complete()
        self.assertEqual([m["role"] for m in result["messages"]], ["user", "assistant"])
        self.assertIn("两下轻响", result["messages"][-1]["content"])
        self.assertEqual(result["messages"][-1]["actions"], [])
        self.assertEqual(result["active_run"]["status"], "success")
        self.assertEqual(self.reference(end=len(MANUSCRIPT)), before)
        call = Provider.calls[-1]
        self.assertEqual(call["path"], "/normal/chat/completions")
        self.assertEqual(call["authorization"], "Bearer " + PRIVATE_KEY)
        self.assertEqual(call["payload"]["model"], MODEL)
        self.assertTrue(call["payload"]["stream"])
        self.assertEqual(call["ledger"][:2], ("dispatched", "running"))
        self.assertEqual(call["payload"]["messages"][0]["role"], "system")
        self.assertEqual(self.provider_material()["current_chapter"]["body"], MANUSCRIPT)
        with closing(server.connect()) as conn:
            ledger = conn.execute("SELECT status FROM app_quota_reservation").fetchone()[0]
        self.assertEqual(ledger, "consumed")

    def test_proposals_wait_for_confirmation_and_open_does_not_consume_quota(self):
        result = self.complete("actions")
        actions = result["messages"][-1]["actions"]
        self.assertEqual({a["kind"] for a in actions}, {"continue", "inspect", "plan", "open"})
        self.assertTrue(all(action["status"] == "proposed" for action in actions))
        self.assertEqual(self.counts()[3], 0)
        before = self.counts()
        navigation = next(action for action in actions if action["kind"] == "open")
        status, confirmed = self.confirm(navigation)
        self.assertEqual(status, 200, confirmed)
        self.assertEqual(confirmed["action"]["result"]["view"], "memory")
        self.assertEqual(confirmed["action"]["status"], "confirmed")
        self.assertEqual(self.counts(), before)
        status, duplicate = self.confirm(navigation)
        self.assertEqual(status, 200, duplicate)
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(Provider.calls), 1)

    def test_confirmed_writing_actions_enter_existing_durable_workflows_once(self):
        result = self.complete("actions")
        for action in result["messages"][-1]["actions"]:
            if action["kind"] == "open":
                continue
            with self.subTest(kind=action["kind"]):
                before = self.counts()
                status, outcome = self.confirm(action)
                self.assertEqual(status, 200, outcome)
                session = outcome["writing_session"]
                self.assertEqual(session["mode"], action["kind"])
                self.assertEqual(session["status"], "awaiting_authorization")
                self.assertEqual(session["calls_used"], 0)
                self.assertEqual(outcome["action"]["status"], "confirmed")
                self.assertEqual(outcome["action"]["result"]["writing_session_id"], session["id"])
                after = self.counts()
                self.assertEqual(after[3], before[3] + 1)
                self.assertEqual(after[4:], before[4:])
                status, duplicate = self.confirm(action)
                self.assertEqual(status, 200, duplicate)
                self.assertTrue(duplicate["idempotent"])
                self.assertEqual(duplicate["action"]["result"]["writing_session_id"], session["id"])
                self.assertEqual(self.counts(), after)
        self.assertEqual(len(Provider.calls), 1)

    def test_selected_polish_creates_one_scoped_job_with_exact_quote(self):
        source = self.reference()
        result = self.complete("polish", references=[source])
        action = result["messages"][-1]["actions"][0]
        before = self.counts()
        status, outcome = self.confirm(action)
        self.assertEqual(status, 200, outcome)
        self.assertEqual(outcome["job"]["task"], "polish")
        self.assertEqual(outcome["job"]["source"], source)
        self.assertEqual(outcome["action"]["result"]["job_id"], outcome["job"]["id"])
        self.assertEqual(self.counts()[4:], tuple(value + 1 for value in before[4:]))
        after = self.counts()
        status, repeated = self.confirm(action)
        self.assertEqual(status, 200, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.counts(), after)
        self.assertEqual(len(Provider.calls), 1)

    def test_concurrent_action_confirmations_create_one_writing_session(self):
        result = self.complete("actions")
        action = next(a for a in result["messages"][-1]["actions"] if a["kind"] == "continue")
        gate, outcomes = threading.Barrier(3), []

        def confirm():
            gate.wait(timeout=5)
            outcomes.append(self.confirm(action))

        threads = [threading.Thread(target=confirm) for _ in range(2)]
        for thread in threads:
            thread.start()
        gate.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(status == 200 for status, _ in outcomes), outcomes)
        self.assertEqual(len({
            outcome["action"]["result"]["writing_session_id"] for _, outcome in outcomes}), 1)
        self.assertEqual(self.counts(), (1, 1, 4, 1, 1, 1, 1))
        self.assertEqual(len(Provider.calls), 1)

    def test_chat_without_a_chapter_sends_only_minimal_verified_context(self):
        result = self.complete(include_chapter=False, chapter_id=None)
        self.assertEqual(result["messages"][-1]["status"], "success")
        material = self.provider_material()
        self.assertEqual(material["project"]["id"], self.project)
        self.assertFalse(material["chapter_context_included"])
        self.assertNotIn("body", material.get("current_chapter", {}))
        self.assertNotIn(MANUSCRIPT, json.dumps(material, ensure_ascii=False))

    def test_no_chapter_chat_cannot_propose_unsourced_writing_tasks(self):
        result = self.complete("actions", include_chapter=False, chapter_id=None)
        self.assertEqual([a["kind"] for a in result["messages"][-1]["actions"]], ["open"])
        self.assertEqual(self.counts()[3], 0)

    def test_reference_only_chat_drops_actions_that_require_unseen_full_chapter(self):
        source = self.reference()
        result = self.complete("reference-only", include_chapter=False, references=[source])
        actions = result["messages"][-1]["actions"]
        self.assertEqual([a["kind"] for a in actions], ["polish", "open"])
        self.assertEqual(actions[0]["label"], "修订原句")
        request = Provider.calls[-1]["payload"]["messages"][-1]["content"]
        self.assertNotIn(MANUSCRIPT, request)
        self.assertIn(source["range"]["quote"], request)
        self.assertIn('"chapter_context_included":false', request)
        material = self.provider_material()
        self.assertNotIn("body", material["current_chapter"])
        self.assertEqual(material["quoted_manuscript"], [source])
        status, confirmed = self.confirm(actions[0])
        self.assertEqual(status, 200, confirmed)
        self.assertEqual(confirmed["job"]["source"], source)

    def test_changed_source_blocks_queued_dispatch_and_proposed_action(self):
        self.configure("actions")
        session = self.create()["session"]
        status, queued = self.send(session)
        self.assertEqual(status, 202, queued)
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": MANUSCRIPT + "\n门开了。"})
        job, _ = self.process(queued)
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["result"]["billing_state"], "not_dispatched")
        self.assertEqual(Provider.calls, [])
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation").fetchone()[0], "released")

        status, queued = self.send(session, request_id="current_source_message")
        self.assertEqual(status, 202, queued)
        job, result = self.process(queued)
        self.assertEqual(job["status"], "success", job)
        action = next(a for a in result["messages"][-1]["actions"] if a["kind"] == "continue")
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": MANUSCRIPT + "\n门又关上。"})
        before = self.counts()
        status, rejected = self.confirm(action)
        self.assertEqual(status, 400, rejected)
        self.assertIn("原稿已变化", rejected["error"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(Provider.calls), 1)

    def test_crossed_chat_marker_is_rejected_before_provider_dispatch(self):
        self.configure()
        first, second = self.create("marker_first")["session"], self.create("marker_second")["session"]
        status, first_queued = self.send(first, request_id="marker_first_message")
        self.assertEqual(status, 202, first_queued)
        status, second_queued = self.send(second, request_id="marker_second_message")
        self.assertEqual(status, 202, second_queued)
        with closing(server.connect()) as conn, conn:
            first_job = conn.execute("SELECT * FROM app_ai_job WHERE id=?",
                                     (first_queued["active_run"]["id"],)).fetchone()
            second_job = conn.execute("SELECT * FROM app_ai_job WHERE id=?",
                                      (second_queued["active_run"]["id"],)).fetchone()
            context = json.loads(first_job["context_json"])
            context["_agent_chat"] = json.loads(second_job["context_json"])["_agent_chat"]
            conn.execute("UPDATE app_ai_job SET context_json=? WHERE id=?",
                         (server.json_text(context), first_job["id"]))
        job, result = self.process(first_queued)
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["result"]["billing_state"], "not_dispatched")
        self.assertEqual(result["messages"][-1]["actions"], [])
        self.assertEqual(Provider.calls, [])
        self.assertEqual(self.read(second)["active_run"]["status"], "queued")
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                (first_job["ai_run_id"],)).fetchone()[0], "released")

    def test_modified_reference_quote_is_rejected_before_dispatch(self):
        self.configure("polish")
        session = self.create()["session"]
        source = self.reference()
        status, queued = self.send(session, references=[source])
        self.assertEqual(status, 202, queued)
        with closing(server.connect()) as conn, conn:
            job = conn.execute("SELECT * FROM app_ai_job WHERE id=?",
                               (queued["active_run"]["id"],)).fetchone()
            context = json.loads(job["context_json"])
            context["quoted_manuscript"][0]["range"]["quote"] = "伪造的旧稿原句"
            conn.execute("UPDATE app_ai_job SET context_json=? WHERE id=?",
                         (server.json_text(context), job["id"]))
        job, _ = self.process(queued)
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["result"]["billing_state"], "not_dispatched")
        self.assertEqual(Provider.calls, [])

    def test_matching_tampered_reference_snapshots_still_fail_before_dispatch(self):
        self.configure("polish")
        session = self.create()["session"]
        status, queued = self.send(session, references=[self.reference()])
        self.assertEqual(status, 202, queued)
        with closing(server.connect()) as conn, conn:
            job = conn.execute("SELECT * FROM app_ai_job WHERE id=?",
                               (queued["active_run"]["id"],)).fetchone()
            context = json.loads(job["context_json"])
            context["quoted_manuscript"][0]["range"]["quote"] = "伪造的旧稿原句"
            encoded = server.json_text(context)
            conn.execute("UPDATE app_ai_job SET context_json=? WHERE id=?",
                         (encoded, job["id"]))
            conn.execute("UPDATE app_agent_chat_turn SET context_json=? WHERE job_id=?",
                         (encoded, job["id"]))
        job, result = self.process(queued)
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["result"]["billing_state"], "not_dispatched")
        self.assertEqual(result["messages"][-1]["status"], "failed")
        self.assertEqual(result["messages"][-1]["actions"], [])
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation").fetchone()[0], "released")
        self.assertEqual(Provider.calls, [])

    def test_missing_persisted_turn_ends_job_without_provider_dispatch(self):
        self.configure()
        session = self.create()["session"]
        status, queued = self.send(session)
        self.assertEqual(status, 202, queued)
        job_id = queued["active_run"]["id"]
        with closing(server.connect()) as conn, conn:
            conn.execute("DELETE FROM app_agent_chat_turn WHERE job_id=?", (job_id,))
        job = server.process_ai_job(job_id)
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["result"]["billing_state"], "not_dispatched")
        self.assertFalse(job["result"]["safe_to_apply"])
        self.assertEqual(self.read(session)["messages"], [])
        with closing(server.connect()) as conn:
            ledger = conn.execute(
                "SELECT j.status,a.status,q.status FROM app_ai_job j "
                "JOIN ai_run a ON a.id=j.ai_run_id "
                "JOIN app_quota_reservation q ON q.ai_run_id=a.id WHERE j.id=?",
                (job_id,)).fetchone()
            self.assertEqual(tuple(ledger), ("failed", "failed", "released"))
        self.assertEqual(server.process_ai_job(job_id)["status"], "failed")
        self.assertEqual(Provider.calls, [])

    def test_manuscript_change_during_reply_cannot_publish_stale_proposals(self):
        self.configure("held-actions")
        session = self.create()["session"]
        status, queued = self.send(session)
        self.assertEqual(status, 202, queued)
        completed = []
        job = threading.Thread(target=lambda: completed.append(
            server.process_ai_job(queued["active_run"]["id"])))
        self.jobs.append(job)
        job.start()
        self.assertTrue(Provider.started.wait(timeout=5))
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": MANUSCRIPT + "\n门开了，里面空无一人。"})
        Provider.release.set()
        job.join(timeout=8)
        self.assertFalse(job.is_alive())
        self.assertEqual(completed[0]["status"], "failed", completed)
        self.assertFalse(completed[0]["result"]["safe_to_apply"])
        historical = self.read(session)
        self.assertEqual(historical["messages"][-1]["actions"], [])
        self.assertEqual(historical["messages"][-1]["content"], "")
        self.assertEqual(len(Provider.calls), 1)

    def test_other_projects_and_branches_cannot_read_or_submit_chat_artifacts(self):
        result = self.complete("actions")
        session, run = result["session"], result["active_run"]
        action = result["messages"][-1]["actions"][0]
        with closing(server.connect()) as conn, conn:
            other = server.new_project(conn, {"title": "隔离作品"})["project"]
            conn.execute("INSERT INTO branch VALUES(?,?,NULL,NULL,NULL,'active','{}')",
                         ("chat_other_branch", self.project))
        for scope in ({"project": other["id"], "branch": other["branch_id"]},
                      {"branch": "chat_other_branch"}):
            with self.subTest(scope=scope):
                status, listed = self.request("GET", "/api/agent-chat/sessions", **scope)
                self.assertEqual(status, 200, listed)
                self.assertEqual(listed["sessions"], [])
                routes = [
                    ("GET", f"/api/agent-chat/sessions/{session['id']}", None),
                    ("GET", f"/api/agent-chat/runs/{run['id']}", None),
                    ("POST", f"/api/agent-chat/sessions/{session['id']}/messages",
                     {"request_id": "scope_message", "content": "访问其他作品", "chapter_id": self.chapter}),
                    ("POST", f"/api/agent-chat/runs/{run['id']}/cancel", {}),
                    ("POST", f"/api/agent-chat/actions/{action['id']}/confirm", {"confirm": True}),
                    ("POST", f"/api/agent-chat/actions/{action['id']}/dismiss", {}),
                ]
                for method, route, data in routes:
                    with self.subTest(route=route):
                        before = self.snapshot()
                        status, rejected = self.request(method, route, data, **scope)
                        self.assertEqual(status, 404, rejected)
                        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.calls), 1)

    def test_restore_is_read_only_and_local_connection_is_not_transferred(self):
        result = self.complete("actions")
        old_session = result["session"]
        with closing(server.connect()) as conn, conn:
            payload = backups.capture(conn, self.project)
            self.assertNotIn("app_agent_connection", payload["tables"])
            restored = backups.transplant(conn, payload, "chat_restored_book")
        mapping = restored["identity_map"]
        scope = {"project": restored["project_id"], "branch": restored["branch_id"]}
        session_id = mapping[old_session["id"]]
        status, historical = self.request("GET", f"/api/agent-chat/sessions/{session_id}", **scope)
        self.assertEqual(status, 200, historical)
        self.assertTrue(historical["session"]["restored_read_only"])
        self.assertEqual(historical["messages"], [
            {**message, "id": mapping[message["id"].rsplit("_", 1)[0]] + "_" +
             message["id"].rsplit("_", 1)[1],
             **({"actions": [{**action, "id": mapping[action["id"]],
                              "chapter_id": mapping[action["chapter_id"]]}
                             for action in message["actions"]]}
                if "actions" in message else {})}
            for message in result["messages"]
        ])
        action_id = historical["messages"][-1]["actions"][0]["id"]
        before = self.snapshot()
        for route, data in (
            (f"/api/agent-chat/sessions/{session_id}/messages",
             {"request_id": "restored_send", "content": "继续历史聊天。"}),
            (f"/api/agent-chat/actions/{action_id}/confirm", {"confirm": True}),
            (f"/api/agent-chat/actions/{action_id}/dismiss", {}),
            (f"/api/agent-chat/runs/{historical['active_run']['id']}/cancel", {}),
        ):
            with self.subTest(route=route):
                status, rejected = self.request("POST", route, data, **scope)
                self.assertEqual(status, 400, rejected)
                self.assertIn("仅供回看", rejected["error"])
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.calls), 1)

    def test_public_list_and_get_do_not_expose_context_prompts_or_credentials(self):
        result = self.complete()
        with closing(server.connect()) as conn:
            turn = conn.execute("SELECT * FROM app_agent_chat_turn").fetchone()
            context = json.loads(turn["context_json"])
        self.assertIn("_material_source", context)
        self.assertIn("_guidance_source", context)
        self.assertIn("_agent_chat", context)
        for route in ("/api/agent-chat/sessions",
                      f"/api/agent-chat/sessions/{result['session']['id']}",
                      f"/api/agent-chat/runs/{result['active_run']['id']}"):
            with self.subTest(route=route):
                status, public = self.request("GET", route)
                self.assertEqual(status, 200, public)
                serialized = json.dumps(public, ensure_ascii=False)
                for secret in (PRIVATE_KEY, "context_json", "provider_config", "_credential_ref",
                               "_material_source", "_guidance_source", "_agent_chat",
                               chat.SYSTEM, MANUSCRIPT):
                    self.assertNotIn(secret, serialized)

    def test_queued_cancel_releases_quota_and_does_not_resend_same_request(self):
        self.configure()
        session = self.create()["session"]
        status, queued = self.send(session)
        self.assertEqual(status, 202, queued)
        job_id = queued["active_run"]["id"]
        before = self.counts()
        status, canceled = self.request("POST", f"/api/agent-chat/runs/{job_id}/cancel", {})
        self.assertEqual(status, 200, canceled)
        self.assertEqual(canceled["active_run"]["status"], "canceled")
        self.assertEqual(canceled["messages"][-1]["status"], "canceled")
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute("SELECT status FROM app_quota_reservation").fetchone()[0],
                             "released")
        status, repeated = self.send(session)
        self.assertEqual(status, 202, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(server.process_ai_job(job_id)["status"], "canceled")
        self.assertEqual(Provider.calls, [])
        self.assertEqual(self.worker.call_count, 1)

    def test_running_cancel_retains_unknown_billing_and_never_retries_automatically(self):
        self.configure("held")
        session = self.create()["session"]
        status, queued = self.send(session)
        self.assertEqual(status, 202, queued)
        job_id, completed = queued["active_run"]["id"], []
        job = threading.Thread(target=lambda: completed.append(server.process_ai_job(job_id)))
        self.jobs.append(job)
        job.start()
        self.assertTrue(Provider.started.wait(timeout=5))
        status, canceled = self.request("POST", f"/api/agent-chat/runs/{job_id}/cancel", {})
        self.assertEqual(status, 200, canceled)
        Provider.release.set()
        job.join(timeout=8)
        self.assertFalse(job.is_alive())
        self.assertEqual(completed[0]["status"], "canceled", completed)
        self.assertEqual(completed[0]["result"]["billing_state"], "unknown")
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation").fetchone()[0], "unknown")
        before = self.counts()
        status, repeated = self.send(session)
        self.assertEqual(status, 202, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(server.process_ai_job(job_id)["status"], "canceled")
        self.assertEqual(len(Provider.calls), 1)

    def test_unexpected_worker_failure_closes_chat_and_preserves_billing_evidence(self):
        self.configure()
        for was_dispatched in (False, True):
            with self.subTest(was_dispatched=was_dispatched):
                suffix = "dispatched" if was_dispatched else "queued"
                session = self.create("worker_session_" + suffix)["session"]
                status, queued = self.send(session, request_id="worker_message_" + suffix)
                self.assertEqual(status, 202, queued)
                job_id = queued["active_run"]["id"]
                if was_dispatched:
                    with closing(server.connect()) as conn, conn:
                        conn.execute(
                            "UPDATE app_ai_job SET status='running',attempt=1,started_at=?,"
                            "result_json=? WHERE id=?",
                            (server.now(), server.json_text({
                                "text": "已返回但未完成的讨论片段。",
                                "diagnostic": "synthetic-partial-response",
                                "safe_to_apply": True,
                            }), job_id))
                        conn.execute(
                            "UPDATE ai_run SET status='running' WHERE id="
                            "(SELECT ai_run_id FROM app_ai_job WHERE id=?)", (job_id,))
                        conn.execute(
                            "UPDATE app_quota_reservation SET status='dispatched' WHERE ai_run_id="
                            "(SELECT ai_run_id FROM app_ai_job WHERE id=?)", (job_id,))
                with patch.object(server, "process_ai_job",
                                  side_effect=RuntimeError("synthetic worker interruption")):
                    server._ai_worker(job_id)
                historical = self.read(session)
                self.assertEqual(historical["active_run"]["status"], "failed")
                self.assertEqual(historical["messages"][-1]["status"], "failed")
                self.assertEqual(historical["messages"][-1]["content"], "")
                self.assertEqual(historical["messages"][-1]["actions"], [])
                with closing(server.connect()) as conn:
                    state = conn.execute(
                        "SELECT j.status,j.result_json,a.status,a.consent_json,q.status,t.status "
                        "FROM app_ai_job j JOIN ai_run a ON a.id=j.ai_run_id "
                        "JOIN app_quota_reservation q ON q.ai_run_id=a.id "
                        "JOIN app_agent_chat_turn t ON t.job_id=j.id WHERE j.id=?",
                        (job_id,)).fetchone()
                expected_billing = "unknown" if was_dispatched else "not_dispatched"
                self.assertEqual(state[0], "failed")
                result = json.loads(state[1])
                self.assertEqual(result["billing_state"], expected_billing)
                self.assertFalse(result["safe_to_apply"])
                if was_dispatched:
                    self.assertEqual(result["text"], "已返回但未完成的讨论片段。")
                    self.assertEqual(result["diagnostic"], "synthetic-partial-response")
                self.assertEqual(state[2], "unknown" if was_dispatched else "failed")
                self.assertEqual(json.loads(state[3])["billing_state"], expected_billing)
                self.assertEqual(state[4], "unknown" if was_dispatched else "released")
                self.assertEqual(state[5], "failed")
                before = self.counts()
                status, repeated = self.send(session, request_id="worker_message_" + suffix)
                self.assertEqual(status, 202, repeated)
                self.assertTrue(repeated["idempotent"])
                self.assertEqual(self.counts(), before)
                self.assertEqual(server.process_ai_job(job_id)["status"], "failed")
        self.assertEqual(Provider.calls, [])

    def test_interrupted_or_invalid_model_reply_cannot_create_actions(self):
        for case in ("eof", "bad-action"):
            with self.subTest(case=case):
                self.configure(case)
                session = self.create(request_id="session_" + case)["session"]
                status, queued = self.send(session, request_id="message_" + case)
                self.assertEqual(status, 202, queued)
                job, result = self.process(queued)
                self.assertEqual(job["status"], "failed", job)
                self.assertFalse(job["result"]["safe_to_apply"])
                self.assertEqual(result["messages"][-1]["actions"], [])
                self.assertEqual(result["messages"][-1]["content"], "")
                with closing(server.connect()) as conn:
                    self.assertEqual(conn.execute(
                        "SELECT COUNT(*) FROM app_agent_chat_action").fetchone()[0], 0)
                before = len(Provider.calls)
                server.process_ai_job(queued["active_run"]["id"])
                self.assertEqual(len(Provider.calls), before)

    def test_dismissed_actions_require_no_calls_and_cannot_be_confirmed(self):
        result = self.complete("actions")
        action = result["messages"][-1]["actions"][0]
        before = self.counts()
        route = f"/api/agent-chat/actions/{action['id']}/dismiss"
        status, dismissed = self.request("POST", route, {})
        self.assertEqual(status, 200, dismissed)
        self.assertEqual(dismissed["action"]["status"], "dismissed")
        status, repeated = self.request("POST", route, {})
        self.assertEqual(status, 200, repeated)
        status, rejected = self.confirm(action)
        self.assertEqual(status, 400, rejected)
        self.assertIn("已放弃", rejected["error"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(Provider.calls), 1)


def main():
    started = datetime.now(timezone.utc).isoformat()
    source_before = source_fingerprints()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ChatTests))
    source_after = source_fingerprints()
    report = {
        "passed": result.wasSuccessful(), "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "local_provider_calls": ChatTests.total_local_calls,
        "paid_provider_calls": 0, "real_database_access": False,
        "scope": REPORT_SCOPE,
        "claim_limit": "service correctness only; not browser appearance, literary quality or competitiveness",
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "runtime": sys.version.split()[0],
        "source_sha256_before": source_before, "source_sha256_after": source_after,
        "source_unchanged": source_before == source_after,
    }
    report_path = os.environ.get("MOYU_AGENT_CHAT_REPORT")
    if report_path:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
