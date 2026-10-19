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

from backend import moyu_agent_chat as chat
from backend import moyu_backups as backups
from backend import moyu_settings as settings
from backend import moyu_workspace as workspace
from backend import server


MANUSCRIPT = "林岚握住旧门的把手。\n门里传来两下轻响。"
PRIVATE_KEY = "synthetic-local-chat-provider-private-key"
MODEL = "local-chat-fixture"
REPORT_SCOPE = (
    "real application HTTP, isolated temporary SQLite and local HTTP provider; "
    "no production data, real credentials or paid provider calls"
)
SOURCE_FILES = (
    "backend/server.py", "backend/moyu_agent_chat.py", "backend/moyu_agent_service.py", "backend/moyu_agent_settings.py",
    "backend/moyu_backups.py", "backend/moyu_settings.py", "backend/moyu_provider.py", "backend/moyu_materials.py",
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
        if case in {"actions", "held-actions", "held-length"}:
            actions = [
                {"kind": kind, "label": label, "instruction": "检查门内声音并保持林岚视角。"}
                for kind, label in (("continue", "接着写"), ("inspect", "检查本章"),
                                    ("plan", "规划后续"))
            ] + [{"kind": "open", "label": "查看记忆", "instruction": "", "view": "memory"}]
        elif case == "next-chapter":
            actions = [{"kind": "next_chapter", "label": "继续下一章",
                        "instruction": "本章已经写完，请先归档，再打开下一章。"}]
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
        elif case in {"inspiration", "held-inspiration"}:
            actions = [
                {"kind": "save_inspiration", "label": "保存铜盒方案", "instruction": "保留待选方案。",
                 "title": "铜盒与旧账", "content": "设想让铜盒保存一份有代价的旧账；尚未发生，等待作者采用。"},
                {"kind": "save_inspiration", "label": "保存声音方案", "instruction": "保留另一种方向。",
                 "title": "门内的声音", "content": "另一种设想：门内声音来自录音，主角必须判断录音的时间。"},
                {"kind": "continue", "label": "直接续写", "instruction": "继续尚未提供的章节。"},
                {"kind": "open", "label": "查看设定库", "instruction": "", "view": "world"},
            ]
        elif case == "bad-inspiration":
            actions = [
                {"kind": "save_inspiration", "label": "缺少内容", "instruction": "", "title": "空方案"},
                {"kind": "save_inspiration", "label": "内容越界", "instruction": "",
                 "title": "过长方案", "content": "甲" * 6001},
                {"kind": "save_inspiration", "label": "标题为空", "instruction": "",
                 "title": "  ", "content": "尚未采用的方案。"},
                {"kind": "save_inspiration", "label": "混入未知字段", "instruction": "",
                 "title": "未知字段", "content": "尚未采用的方案。", "execute": True},
            ]
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
            if case in {"held", "held-actions", "held-inspiration", "held-eof", "held-length"}:
                self.started.set()
                self.release.wait(timeout=8)
            if case in {"eof", "held-eof"}:
                return
            self.event({"choices": [{"index": 0, "delta": {"content": reply[midpoint:]},
                                      "finish_reason": "length" if case == "held-length" else "stop"}]})
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

    def complete_with_change(self, change, *, case="held-actions", **overrides):
        self.configure(case)
        Provider.started.clear()
        Provider.release.clear()
        session = self.create("held_session_" + str(len(Provider.calls)))["session"]
        status, queued = self.send(session, **overrides)
        self.assertEqual(status, 202, queued)
        completed = []
        job = threading.Thread(target=lambda: completed.append(
            server.process_ai_job(queued["active_run"]["id"])))
        self.jobs.append(job)
        job.start()
        try:
            self.assertTrue(Provider.started.wait(timeout=5))
            change(queued)
        finally:
            Provider.release.set()
            job.join(timeout=10)
        self.assertFalse(job.is_alive())
        self.assertEqual(len(completed), 1, completed)
        return completed[0], self.read(session)

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

    def interrupted_writing_confirmation(self):
        result = self.complete("actions")
        action = next(item for item in result["messages"][-1]["actions"]
                      if item["kind"] == "continue")
        actual_request = chat.writing_agent.request
        committed = {}

        def interrupted_after_commit(*args, **kwargs):
            outcome = actual_request(*args, **kwargs)
            committed.update(outcome)
            raise RuntimeError("synthetic interruption after committed writing session")

        with closing(server.connect()) as conn, conn:
            with patch.object(chat.writing_agent, "request", interrupted_after_commit):
                with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                    chat.confirm_action(server, conn, action["id"], {"confirm": True})
        with closing(server.connect()) as conn:
            saved = conn.execute("SELECT status,result_json FROM app_agent_chat_action WHERE id=?",
                                 (action["id"],)).fetchone()
        self.assertEqual(saved["status"], "confirming")
        self.assertEqual(json.loads(saved["result_json"]), {})
        self.assertEqual(committed["session"]["status"], "awaiting_authorization")
        return action, committed["session"]

    def assert_interrupted_confirmation_recovers(self, change):
        action, original = self.interrupted_writing_confirmation()
        with closing(server.connect()) as conn, conn:
            if change == "manuscript":
                server.update_chapter(conn, self.chapter, {
                    "body": MANUSCRIPT + "\n作者在上一次确认中断后继续改稿。",
                })
            elif change == "materials":
                changed = conn.execute("UPDATE app_entity_meta SET description='作者补充了人物背景'")
                self.assertGreater(changed.rowcount, 0)
            elif change == "settings":
                settings.save_settings(conn, self.project, {
                    "base_url": self.base + "/changed", "model": MODEL + "-changed",
                    "api_key": PRIVATE_KEY, "daily_limit": 100,
                })
            else:
                self.fail("unexpected isolated mutation")
        with closing(server.connect()) as conn:
            saved_task = dict(conn.execute(
                "SELECT * FROM app_agent_session WHERE id=?", (original["id"],)).fetchone())
        before, worker_calls = self.counts(), self.worker.call_count
        status, recovered = self.confirm(action)
        self.assertEqual(status, 200, recovered)
        self.assertEqual(recovered["action"]["status"], "confirmed")
        self.assertEqual(recovered["action"]["result"]["writing_session_id"], original["id"])
        self.assertEqual(recovered["writing_session"]["id"], original["id"])
        self.assertEqual(recovered["writing_session"]["status"], "awaiting_authorization")
        with closing(server.connect()) as conn:
            current_task = dict(conn.execute(
                "SELECT * FROM app_agent_session WHERE id=?", (original["id"],)).fetchone())
        self.assertEqual(current_task, saved_task)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.worker.call_count, worker_calls)
        status, duplicate = self.confirm(action)
        self.assertEqual(status, 200, duplicate)
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(duplicate["action"]["result"]["writing_session_id"], original["id"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.worker.call_count, worker_calls)
        self.assertEqual(len(Provider.calls), 1)

    def test_interrupted_confirmation_recovers_original_task_after_manuscript_changes(self):
        self.assert_interrupted_confirmation_recovers("manuscript")

    def test_interrupted_confirmation_recovers_original_task_after_material_changes(self):
        self.assert_interrupted_confirmation_recovers("materials")

    def test_interrupted_confirmation_recovers_original_task_after_model_settings_change(self):
        self.assert_interrupted_confirmation_recovers("settings")

    def test_interrupted_confirmation_rejects_mismatched_or_restored_existing_task(self):
        with closing(server.connect()) as conn, conn:
            other_chapter = server.create_chapter(conn, {
                "title": "其他章节", "body": "渡口的船工正在收起灯笼。",
            })
        action, original = self.interrupted_writing_confirmation()
        with closing(server.connect()) as conn:
            saved_task = dict(conn.execute(
                "SELECT * FROM app_agent_session WHERE id=?", (original["id"],)).fetchone())
        wrong_mode = json.loads(saved_task["plan_json"])
        wrong_mode["mode"] = "inspect"
        changes = (
            ("kind", "plan_json", server.json_text(wrong_mode)),
            ("chapter", "chapter_id", other_chapter),
            ("query", "query", "另一条不同的创作要求"),
            ("restored", "restored_read_only", 1),
        )
        for name, column, value in changes:
            with self.subTest(mismatch=name):
                with closing(server.connect()) as conn, conn:
                    conn.execute(f"UPDATE app_agent_session SET {column}=? WHERE id=?",
                                 (value, original["id"]))
                before = self.snapshot()
                worker_calls = self.worker.call_count
                status, rejected = self.confirm(action)
                self.assertEqual(status, 400, rejected)
                self.assertTrue(rejected.get("error"))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.worker.call_count, worker_calls)
                with closing(server.connect()) as conn, conn:
                    conn.execute(f"UPDATE app_agent_session SET {column}=? WHERE id=?",
                                 (saved_task[column], original["id"]))
        self.assertEqual(len(Provider.calls), 1)

    def test_confirming_without_a_committed_task_still_rejects_changed_source(self):
        result = self.complete("actions")
        action = next(item for item in result["messages"][-1]["actions"]
                      if item["kind"] == "continue")
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_agent_chat_action SET status='confirming' WHERE id=?",
                         (action["id"],))
            server.update_chapter(conn, self.chapter, {"body": MANUSCRIPT + "\n作者改动了原稿。"})
        before, worker_calls = self.snapshot(), self.worker.call_count
        status, rejected = self.confirm(action)
        self.assertEqual(status, 400, rejected)
        self.assertIn("原稿已变化", rejected["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.counts()[3], 0)
        self.assertEqual(self.worker.call_count, worker_calls)
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

    def test_chat_without_a_chapter_sends_verified_project_materials_without_prose(self):
        result = self.complete(include_chapter=False, chapter_id=None)
        self.assertEqual(result["messages"][-1]["status"], "success")
        material = self.provider_material()
        self.assertEqual(material["project"]["id"], self.project)
        self.assertFalse(material["chapter_context_included"])
        self.assertTrue(material["project_materials_included"])
        self.assertTrue(material["project"]["title"])
        self.assertNotIn("body", material.get("current_chapter") or {})
        self.assertNotIn(MANUSCRIPT, json.dumps(material, ensure_ascii=False))
        self.assertEqual(material["previous_chapters"], [])
        self.assertEqual(material["retrieved_chapters"], [])
        self.assertEqual(material["confirmed_memory"], [])
        self.assertTrue(result["context_summary"]["project_materials_included"])
        self.assertFalse(result["context_summary"]["chapter_included"])

    def test_no_chapter_discussion_contains_relevant_setting_and_separate_author_plans(self):
        card = self.request("POST", "/api/records", {
            "type": "物品", "name": "铜盒", "description": "铜盒需要两枚不同的钥匙。"
        })[1]
        plan = self.request("POST", "/api/records", {
            "type": "灵感", "name": "铜盒交换", "description": "设想以铜盒换回失踪者。",
            "epistemic_mode": "author_plan", "branch_id": self.branch,
        })[1]
        with closing(server.connect()) as conn, conn:
            workspace.put_blueprint(conn, self.project, self.branch, {
                "expected_version": 0, "blueprint": {
                    "premise": "围绕铜盒展开的悬疑故事。",
                    "outline": "先交换线索，再揭示两把钥匙的代价。",
                },
            })
        result = self.complete(include_chapter=False, chapter_id=None,
                               content="给铜盒交换这个情节想几个具体走向。")
        material = self.provider_material()
        self.assertIn(card["id"], [item["id"] for item in material["story_cards"]])
        saved_plan = next(item for item in material["author_plans"] if item["id"] == plan["id"])
        self.assertEqual(saved_plan["epistemic_mode"], "author_plan")
        self.assertEqual(saved_plan["use"], "planning_only_not_occurred")
        self.assertIn("两把钥匙", material["author_guidance"]["outline"])
        self.assertNotIn(MANUSCRIPT, json.dumps(material, ensure_ascii=False))
        self.assertGreaterEqual(result["context_summary"]["material_count"], 2)
        with closing(server.connect()) as conn:
            context = json.loads(conn.execute("SELECT context_json FROM app_agent_chat_turn").fetchone()[0])
        self.assertIn(("record", card["id"]), {
            (item["kind"], item["id"]) for item in context["_material_source"]["dependencies"]})
        self.assertNotIn("chapter", {
            item["kind"] for item in context["_material_source"]["dependencies"]})

    def test_project_overview_is_bounded_with_large_summary_plans_and_settings(self):
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_project_meta SET summary=? WHERE project_id=?",
                         ("很长的作品简介" * 5000, self.project))
            workspace.put_blueprint(conn, self.project, self.branch, {
                "expected_version": 0, "blueprint": {
                    "premise": "设想" * 3000, "contract": "约定" * 6000,
                    "outline": "规划" * 30000,
                    "volumes": [{"id": "long_volume_" + str(index), "title": "长卷" * 100,
                                 "summary": "分卷设想" * 2000} for index in range(8)],
                },
            })
        for index in range(28):
            status, card = self.request("POST", "/api/records", {
                "type": "灵感" if index % 2 else "物品", "name": f"铜盒{index}",
                "description": "尚待讨论的铜盒方案。" * 1000,
                **({"epistemic_mode": "author_plan"} if index % 2 else {}),
            })
            self.assertEqual(status, 201, card)
        self.complete(include_chapter=False, chapter_id=None, content="比较铜盒的方案。")
        material = self.provider_material()
        self.assertLess(len(json.dumps(material, ensure_ascii=False).encode()), 26000)
        self.assertTrue(material["context_budget"]["truncation"]["applied"]
                        or material["project_material_selection"]["initial_budget_items_omitted"])
        self.assertNotIn(MANUSCRIPT, json.dumps(material, ensure_ascii=False))

    def test_project_material_changes_prevent_unsent_inspiration_dispatch(self):
        self.configure("inspiration")
        session = self.create()["session"]
        status, queued = self.send(session, include_chapter=False, chapter_id=None)
        self.assertEqual(status, 202, queued)
        with closing(server.connect()) as conn, conn:
            workspace.put_blueprint(conn, self.project, self.branch, {
                "expected_version": 0, "blueprint": {"premise": "作者改成了另一部故事。"}})
        job, result = self.process(queued)
        self.assertEqual(job["status"], "failed", job)
        self.assertEqual(job["result"]["billing_state"], "not_dispatched")
        self.assertEqual(Provider.calls, [])
        self.assertEqual(result["messages"][-1]["actions"], [])

    def test_inspiration_confirmation_saves_author_plan_once_without_changing_prose(self):
        before = self.reference(end=len(MANUSCRIPT))
        result = self.complete("inspiration", include_chapter=False, chapter_id=None)
        actions = result["messages"][-1]["actions"]
        self.assertEqual([item["kind"] for item in actions],
                         ["save_inspiration", "save_inspiration", "open"])
        self.assertTrue(actions[0]["title"])
        self.assertIn("尚未发生", actions[0]["content"])
        with closing(server.connect()) as conn:
            before_records = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
            before_events = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
            before_memory = server.memory_rows(conn)
        for index, action in enumerate(actions[:2], start=1):
            status, confirmed = self.confirm(action)
            self.assertEqual(status, 200, confirmed)
            self.assertIsNone(confirmed["writing_session"])
            self.assertIsNone(confirmed["job"])
            receipt = confirmed["action"]["result"]
            self.assertEqual(receipt["view"], "world")
            self.assertEqual(receipt["epistemic_mode"], "author_plan")
            with closing(server.connect()) as conn:
                state = server.state_from_db(conn)
                record = next(item for item in state["records"] if item["id"] == receipt["record_id"])
                self.assertEqual(record["type"], "灵感")
                self.assertEqual(record["name"], action["title"])
                self.assertEqual(record["description"], action["content"])
                self.assertEqual(record["epistemic_mode"], "author_plan")
                self.assertEqual(record["branch_id"], self.branch)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0],
                                 before_records + index)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0],
                                 before_events + index)
                self.assertEqual(state["memory"], before_memory)
                writing_context, _ = server.ai_context(conn, self.chapter, task="continue")
                self.assertNotIn(record["id"], [
                    item["id"] for item in writing_context.get("story_cards") or []])
            status, repeated = self.confirm(action)
            self.assertEqual(status, 200, repeated)
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(repeated["action"]["result"], receipt)
        self.assertEqual(self.reference(end=len(MANUSCRIPT)), before)
        self.assertEqual(len(Provider.calls), 1)
        self.assertEqual(self.counts()[3], 0)
        self.assertEqual(self.counts()[4:], (1, 1, 1))

    def test_concurrent_inspiration_confirmations_create_one_record_and_event(self):
        result = self.complete("inspiration", include_chapter=False, chapter_id=None)
        action = result["messages"][-1]["actions"][0]
        with closing(server.connect()) as conn:
            before_records = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
            before_events = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
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
            outcome["action"]["result"]["record_id"] for _, outcome in outcomes}), 1)
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0],
                             before_records + 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0],
                             before_events + 1)
        self.assertEqual(len(Provider.calls), 1)

    def test_inspiration_save_failure_rolls_back_record_event_and_confirmation(self):
        result = self.complete("inspiration", include_chapter=False, chapter_id=None)
        action = result["messages"][-1]["actions"][0]
        before = self.snapshot()
        with patch.object(server, "append_event", side_effect=ValueError("合成保存失败")):
            status, failed = self.confirm(action)
        self.assertEqual(status, 400, failed)
        self.assertEqual(self.snapshot(), before)
        status, recovered = self.confirm(action)
        self.assertEqual(status, 200, recovered)
        self.assertEqual(recovered["action"]["status"], "confirmed")
        self.assertEqual(len(Provider.calls), 1)

    def test_other_projects_and_branches_cannot_confirm_inspiration(self):
        result = self.complete("inspiration", include_chapter=False, chapter_id=None)
        action = result["messages"][-1]["actions"][0]
        with closing(server.connect()) as conn, conn:
            other = server.new_project(conn, {"title": "隔离灵感作品"})["project"]
            conn.execute("INSERT INTO branch VALUES(?,?,NULL,NULL,NULL,'active','{}')",
                         ("inspiration_other_branch", self.project))
        for scope in ({"project": other["id"], "branch": other["branch_id"]},
                      {"branch": "inspiration_other_branch"}):
            with self.subTest(scope=scope):
                before = self.snapshot()
                status, rejected = self.request(
                    "POST", f"/api/agent-chat/actions/{action['id']}/confirm",
                    {"confirm": True}, **scope)
                self.assertEqual(status, 404, rejected)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.calls), 1)

    def test_inspiration_backup_restore_remaps_record_receipt_and_stays_read_only(self):
        result = self.complete("inspiration", include_chapter=False, chapter_id=None)
        action = result["messages"][-1]["actions"][0]
        status, confirmed = self.confirm(action)
        self.assertEqual(status, 200, confirmed)
        record_id = confirmed["action"]["result"]["record_id"]
        with closing(server.connect()) as conn, conn:
            original_body = server.chapter_rows(conn)[0]["body"]
            payload = backups.capture(conn, self.project)
            restored = backups.transplant(conn, payload, "inspiration_restored_book")
        mapping = restored["identity_map"]
        scope = {"project": restored["project_id"], "branch": restored["branch_id"]}
        status, historical = self.request(
            "GET", f"/api/agent-chat/sessions/{mapping[result['session']['id']]}", **scope)
        self.assertEqual(status, 200, historical)
        self.assertTrue(historical["session"]["restored_read_only"])
        restored_action = next(item for item in historical["messages"][-1]["actions"]
                               if item["id"] == mapping[action["id"]])
        self.assertEqual(restored_action["result"]["record_id"], mapping[record_id])
        status, records = self.request("GET", "/api/records", **scope)
        self.assertEqual(status, 200, records)
        restored_record = next(item for item in records["records"] if item["id"] == mapping[record_id])
        self.assertEqual(restored_record["epistemic_mode"], "author_plan")
        self.assertEqual(restored_record["branch_id"], restored["branch_id"])
        before = self.snapshot()
        status, rejected = self.request(
            "POST", f"/api/agent-chat/actions/{mapping[action['id']]}/confirm",
            {"confirm": True}, **scope)
        self.assertEqual(status, 400, rejected)
        self.assertIn("仅供回看", rejected["error"])
        self.assertEqual(self.snapshot(), before)
        with closing(server.connect()) as conn:
            self.assertEqual(server.chapter_rows(conn)[0]["body"], original_body)
            self.assertEqual(conn.execute(
                "SELECT project_id FROM entity WHERE id=?", (record_id,)).fetchone()[0], self.project)

    def test_inspiration_invalid_payloads_are_not_savable_and_reply_is_preserved(self):
        result = self.complete("bad-inspiration", include_chapter=False, chapter_id=None)
        self.assertTrue(result["messages"][-1]["content"])
        self.assertEqual(result["messages"][-1]["actions"], [])
        self.assertEqual(self.counts()[2:4], (0, 0))

    def test_inspiration_confirmation_rejects_changed_blueprint_and_can_be_dismissed(self):
        result = self.complete("inspiration", include_chapter=False, chapter_id=None)
        action = result["messages"][-1]["actions"][0]
        with closing(server.connect()) as conn, conn:
            workspace.put_blueprint(conn, self.project, self.branch, {
                "expected_version": 0, "blueprint": {"premise": "作者重新设定了主要冲突。"}})
        before = self.snapshot()
        status, rejected = self.confirm(action)
        self.assertEqual(status, 400, rejected)
        self.assertIn("规划已变化", rejected["error"])
        self.assertEqual(self.snapshot(), before)
        status, dismissed = self.request(
            "POST", f"/api/agent-chat/actions/{action['id']}/dismiss", {})
        self.assertEqual(status, 200, dismissed)
        self.assertEqual(self.confirm(action)[0], 400)

    def test_large_historical_quotes_preserve_recent_question_and_reply_with_budget_marker(self):
        long_body = "甲" * 12000 + "\n" + "乙" * 12000
        with closing(server.connect()) as conn, conn:
            server.update_chapter(conn, self.chapter, {"body": long_body})
        sources = [self.reference(0, 12000), self.reference(12001, 24001)]
        result = self.complete(include_chapter=False, references=sources,
                               content="比较这两段，并推荐一个方案。")
        session = result["session"]
        status, queued = self.send(session, request_id="history_followup", include_chapter=False,
                                   content="把推荐方案再具体展开。", references=[])
        self.assertEqual(status, 202, queued)
        job, _ = self.process(queued)
        self.assertEqual(job["status"], "success", job)
        messages = Provider.calls[-1]["payload"]["messages"]
        self.assertEqual([item["role"] for item in messages],
                         ["system", "user", "assistant", "user"])
        self.assertIn("推荐一个方案", messages[1]["content"])
        self.assertIn("两下轻响", messages[2]["content"])
        self.assertNotIn("甲" * 1000, messages[1]["content"])
        self.assertIn('"quote_truncated":true', messages[1]["content"].replace(" ", ""))
        history = self.provider_material()["conversation_history"]
        self.assertTrue(history["truncated"])
        self.assertEqual(history["turns_included"], 1)
        self.assertLessEqual(history["characters_used"], 24000)

    def test_long_recent_reply_keeps_recent_turn_even_when_full_pair_exceeds_history_budget(self):
        self.configure()
        session = self.create()["session"]
        status, queued = self.send(session, content="甲" * 5990, include_chapter=False)
        self.assertEqual(status, 202, queued)
        self.process(queued)
        with closing(server.connect()) as conn, conn:
            conn.execute("UPDATE app_agent_chat_turn SET reply=? WHERE session_id=?",
                         ("最近方案" * 3990, session["id"]))
        status, queued = self.send(session, request_id="long_history_followup",
                                   content="继续最近方案。", include_chapter=False)
        self.assertEqual(status, 202, queued)
        job, _ = self.process(queued)
        self.assertEqual(job["status"], "success", job)
        history = Provider.calls[-1]["payload"]["messages"][1:-1]
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertIn("最近方案", history[1]["content"])
        self.assertIn("history_truncated", history[1]["content"])
        self.assertLessEqual(sum(len(item["content"]) for item in history), 24000)

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
        def change(_queued):
            with closing(server.connect()) as conn, conn:
                server.update_chapter(
                    conn, self.chapter, {"body": MANUSCRIPT + "\n门开了，里面空无一人。"})

        job, historical = self.complete_with_change(change)
        self.assertEqual(job["status"], "success", job)
        self.assertTrue(job["result"]["chat_source_stale"])
        self.assertFalse(job["result"]["safe_to_apply"])
        self.assertEqual(job["result"]["completion_state"], "complete")
        self.assertEqual(job["result"]["structured"]["actions"], [])
        self.assertTrue(historical["messages"][-1]["context_stale"])
        self.assertEqual(historical["messages"][-1]["actions"], [])
        self.assertIn("两下轻响", historical["messages"][-1]["content"])
        raw = job["result"]["text"]
        self.assertEqual(len(json.loads(raw)["actions"]), 4)
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_agent_chat_action").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation").fetchone()[0], "consumed")
            run = conn.execute("SELECT status,output_hash,consent_json FROM ai_run").fetchone()
            self.assertEqual(run["status"], "success")
            self.assertEqual(run["output_hash"], server.hash_text(raw))
            self.assertFalse(json.loads(run["consent_json"])["safe_to_apply"])
            chunks = conn.execute(
                "SELECT text,cumulative_hash FROM app_ai_chunk ORDER BY seq").fetchall()
            self.assertEqual("".join(chunk["text"] for chunk in chunks), raw)
            self.assertEqual(chunks[-1]["cumulative_hash"], server.hash_text(raw))
        self.assertEqual(len(Provider.calls), 1)

    def test_setting_change_during_inspiration_reply_keeps_only_old_reference_text(self):
        status, card = self.request("POST", "/api/records", {
            "type": "物品", "name": "铜盒", "description": "铜盒必须同时使用两把钥匙。"})
        self.assertEqual(status, 201, card)

        def change(_queued):
            status, changed = self.request("PATCH", f"/api/records/{card['id']}", {
                "description": "作者决定铜盒只需要一把钥匙。"})
            self.assertEqual(status, 200, changed)

        job, historical = self.complete_with_change(
            change, case="held-inspiration", include_chapter=False, chapter_id=None,
            content="比较铜盒的不同方案，并给出可以保存的灵感。")
        self.assertIn(card["id"], [item["id"] for item in self.provider_material()["story_cards"]])
        self.assertEqual(job["status"], "success", job)
        self.assertTrue(job["result"]["chat_source_stale"])
        self.assertFalse(job["result"]["safe_to_apply"])
        self.assertEqual(job["result"]["structured"]["actions"], [])
        self.assertTrue(historical["messages"][-1]["context_stale"])
        self.assertIn("两下轻响", historical["messages"][-1]["content"])
        self.assertEqual(historical["messages"][-1]["actions"], [])
        self.assertEqual(self.counts()[2:4], (0, 0))
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM entity WHERE kind='灵感'").fetchone()[0], 0)
        self.assertEqual(len(Provider.calls), 1)

    def test_blueprint_change_during_reply_keeps_text_for_chapter_and_project_discussion(self):
        for include in (True, False):
            with self.subTest(include_chapter=include):
                def change(_queued):
                    with closing(server.connect()) as conn, conn:
                        current = workspace.blueprint_row(conn, self.project, self.branch)
                        workspace.put_blueprint(conn, self.project, self.branch, {
                            "expected_version": current["version"],
                            "blueprint": {"premise": "作者改成另一种主要冲突：" + str(include)}})

                job, historical = self.complete_with_change(
                    change, case="held-inspiration", include_chapter=include,
                    chapter_id=self.chapter if include else None)
                self.assertEqual(job["status"], "success", job)
                self.assertTrue(job["result"]["chat_source_stale"])
                self.assertEqual(job["result"]["structured"]["actions"], [])
                self.assertTrue(historical["messages"][-1]["context_stale"])
                self.assertTrue(historical["messages"][-1]["content"])
                self.assertEqual(historical["messages"][-1]["actions"], [])
                self.assertEqual(self.counts()[2:4], (0, 0))
        self.assertEqual(len(Provider.calls), 2)

    def test_old_reference_reply_is_marked_in_the_next_turn_history(self):
        def change(_queued):
            with closing(server.connect()) as conn, conn:
                server.update_chapter(conn, self.chapter, {"body": MANUSCRIPT + "\n门开了。"})

        _, historical = self.complete_with_change(change)
        status, queued = self.send(historical["session"], request_id="old_reference_followup",
                                   content="按现在的稿件继续讨论。")
        self.assertEqual(status, 202, queued)
        job, current = self.process(queued)
        self.assertEqual(job["status"], "success", job)
        history = Provider.calls[-1]["payload"]["messages"][1:-1]
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        previous = json.loads(history[-1]["content"])
        self.assertTrue(previous["context_stale"])
        self.assertEqual(previous["context_note"], chat.STALE_REPLY_NOTE)
        self.assertEqual(previous["actions"], [])
        self.assertIn("两下轻响", previous["reply"])
        self.assertNotIn("context_stale", current["messages"][-1])
        self.assertEqual(len(Provider.calls), 2)

    def test_saved_chat_tampering_during_reply_still_fails_instead_of_becoming_old_reference(self):
        for field in ("job_context", "both_contexts", "source", "references",
                      "job_chapter", "request_audit", "session_binding"):
            with self.subTest(field=field):
                def tamper(queued):
                    with closing(server.connect()) as conn, conn:
                        job = conn.execute(
                            "SELECT * FROM app_ai_job WHERE id=?",
                            (queued["active_run"]["id"],)).fetchone()
                        context = json.loads(job["context_json"])
                        if field in {"job_context", "both_contexts"}:
                            context["project"]["summary"] = "伪造的发送时资料。"
                            encoded = server.json_text(context)
                            conn.execute("UPDATE app_ai_job SET context_json=? WHERE id=?",
                                         (encoded, job["id"]))
                            if field == "both_contexts":
                                conn.execute(
                                    "UPDATE app_agent_chat_turn SET context_json=? WHERE job_id=?",
                                    (encoded, job["id"]))
                        elif field == "source":
                            source = {**context["_source"], "source_body_hash": "0" * 64}
                            conn.execute(
                                "UPDATE app_agent_chat_turn SET source_json=? WHERE job_id=?",
                                (server.json_text(source), job["id"]))
                        elif field == "references":
                            references = json.loads(json.dumps(context["quoted_manuscript"]))
                            references[0]["range"]["quote"] = "伪造的引用。"
                            conn.execute(
                                "UPDATE app_agent_chat_turn SET references_json=? WHERE job_id=?",
                                (server.json_text(references), job["id"]))
                        elif field == "job_chapter":
                            conn.execute("UPDATE app_ai_job SET chapter_id=NULL WHERE id=?",
                                         (job["id"],))
                        elif field == "request_audit":
                            consent = json.loads(conn.execute(
                                "SELECT consent_json FROM ai_run WHERE id=?",
                                (job["ai_run_id"],)).fetchone()[0])
                            consent["request_audit"]["payload"]["messages"][-1]["content"] += "伪造"
                            conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?",
                                         (server.json_text(consent), job["ai_run_id"]))
                        elif field == "session_binding":
                            conn.execute(
                                "UPDATE app_agent_chat_turn SET session_id=? WHERE job_id=?",
                                (other["id"], job["id"]))

                other = self.create("other_tamper_session_" + field)["session"]
                job, historical = self.complete_with_change(tamper, references=[self.reference()])
                self.assertEqual(job["status"], "failed", job)
                self.assertFalse(job["result"]["safe_to_apply"])
                self.assertFalse(job["result"].get("chat_source_stale", False))
                message = (self.read(other) if field == "session_binding" else historical)["messages"][-1]
                self.assertEqual(message["status"], "failed")
                self.assertEqual(message["content"], "")
                self.assertEqual(message["actions"], [])
                self.assertNotIn("context_stale", message)
                self.assertEqual(self.counts()[2:4], (0, 0))
        self.assertEqual(len(Provider.calls), 7)

    def test_old_reference_reply_survives_backup_restore_without_any_actions(self):
        def change(_queued):
            with closing(server.connect()) as conn, conn:
                server.update_chapter(conn, self.chapter, {"body": MANUSCRIPT + "\n门开了。"})

        job, historical = self.complete_with_change(change)
        with closing(server.connect()) as conn, conn:
            payload = backups.capture(conn, self.project)
            restored = backups.transplant(conn, payload, "old_reference_restored_book")
        mapping = restored["identity_map"]
        scope = {"project": restored["project_id"], "branch": restored["branch_id"]}
        session_id = mapping[historical["session"]["id"]]
        status, recovered = self.request(
            "GET", f"/api/agent-chat/sessions/{session_id}", **scope)
        self.assertEqual(status, 200, recovered)
        self.assertTrue(recovered["session"]["restored_read_only"])
        message = recovered["messages"][-1]
        self.assertEqual(message["content"], historical["messages"][-1]["content"])
        self.assertTrue(message["context_stale"])
        self.assertEqual(message["actions"], [])
        with closing(server.connect()) as conn:
            saved = json.loads(conn.execute(
                "SELECT result_json FROM app_ai_job WHERE id=?", (mapping[job["id"]],)
            ).fetchone()[0])
            self.assertTrue(saved["chat_source_stale"])
            self.assertFalse(saved["safe_to_apply"])
            self.assertEqual(saved["structured"]["actions"], [])
            self.assertEqual(saved["text"], job["result"]["text"])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_agent_chat_action").fetchone()[0], 0)
        before = self.snapshot()
        status, rejected = self.request(
            "POST", f"/api/agent-chat/sessions/{session_id}/messages",
            {"request_id": "restored_old_reply", "content": "从旧回复继续。"}, **scope)
        self.assertEqual(status, 400, rejected)
        self.assertIn("仅供回看", rejected["error"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.calls), 1)

    def test_changed_materials_do_not_turn_canceled_or_incomplete_replies_into_success(self):
        for case in ("held-actions", "held-eof", "held-length"):
            with self.subTest(case=case):
                def change(queued):
                    with closing(server.connect()) as conn, conn:
                        server.update_chapter(
                            conn, self.chapter, {"body": MANUSCRIPT + "\n新稿：" + case})
                    if case == "held-actions":
                        status, canceled = self.request(
                            "POST", f"/api/agent-chat/runs/{queued['active_run']['id']}/cancel", {})
                        self.assertEqual(status, 200, canceled)

                job, historical = self.complete_with_change(change, case=case)
                self.assertEqual(job["status"], "canceled" if case == "held-actions" else "failed", job)
                self.assertFalse(job["result"]["safe_to_apply"])
                self.assertFalse(job["result"].get("chat_source_stale", False))
                self.assertEqual(historical["messages"][-1]["content"], "")
                self.assertEqual(historical["messages"][-1]["actions"], [])
                self.assertNotIn("context_stale", historical["messages"][-1])
                self.assertEqual(self.counts()[2:4], (0, 0))
        self.assertEqual(len(Provider.calls), 3)

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

    def test_interrupted_model_reply_cannot_create_actions(self):
        for case in ("eof",):
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

    def test_invalid_action_does_not_discard_a_complete_useful_reply(self):
        result = self.complete("bad-action")
        self.assertEqual(result["active_run"]["status"], "success")
        self.assertIn("两下轻响", result["messages"][-1]["content"])
        self.assertEqual(result["messages"][-1]["actions"], [])
        with closing(server.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_agent_chat_action").fetchone()[0], 0)
        self.assertEqual(len(Provider.calls), 1)
        server.process_ai_job(result["active_run"]["id"])
        self.assertEqual(len(Provider.calls), 1)

    def test_complete_legacy_format_failure_recovers_read_only_reply_without_actions_or_writes(self):
        result = self.complete()
        job_id = result["active_run"]["id"]
        raw = "```json\n" + json.dumps({
            "reply": "可以让铜盒与主角的旧账产生关联，再比较两种代价。",
            "actions": [{"kind": "save_inspiration", "label": "保存", "instruction": "",
                         "title": "旧账", "content": "一个尚未发生的设想。"}],
        }, ensure_ascii=False) + "\n```"
        for transport, metadata in (
            ("provider_native_sse", {"provider_sse_complete": True}),
            ("provider_json", {}),
        ):
            with self.subTest(transport=transport):
                with closing(server.connect()) as conn, conn:
                    conn.execute(
                        "UPDATE app_ai_job SET status='failed',error=?,result_json=? WHERE id=?",
                        ("旧版本的结构化回复校验失败", server.json_text({
                            "text": raw, "error_code": "invalid_structured_output",
                            "finish_reason": "stop", "completion_state": "unknown",
                            "stream_transport": transport, "safe_to_apply": False,
                            "truncated": True, **metadata,
                        }), job_id))
                    conn.execute(
                        "UPDATE app_agent_chat_turn SET reply='',status='failed' WHERE job_id=?",
                        (job_id,))
                before = self.snapshot()
                historical = self.read(result["session"])
                message = historical["messages"][-1]
                self.assertIn("旧账", message["content"])
                self.assertTrue(message["response_recovered"])
                self.assertEqual(message["status"], "failed")
                self.assertEqual(message["actions"], [])
                self.assertEqual(historical["active_run"]["status"], "failed")
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.calls), 1)
        self.assertEqual(self.counts()[2:4], (0, 0))

    def test_legacy_incomplete_or_unrelated_failure_is_not_recovered(self):
        result = self.complete()
        job_id = result["active_run"]["id"]
        base = {"text": "一条完整可读的建议。", "error_code": "invalid_structured_output",
                "finish_reason": "stop", "provider_sse_complete": True,
                "stream_transport": "provider_native_sse", "safe_to_apply": False}
        for change in (
            {"provider_sse_complete": False}, {"finish_reason": "length"},
            {"error_code": "invalid_response"}, {"text": '{"reply":"未完成'},
            {"text": "<think>只有内部思考</think>"},
        ):
            with self.subTest(change=change):
                with closing(server.connect()) as conn, conn:
                    conn.execute("UPDATE app_ai_job SET status='failed',result_json=? WHERE id=?",
                                 (server.json_text({**base, **change}), job_id))
                    conn.execute("UPDATE app_agent_chat_turn SET reply='',status='failed' WHERE job_id=?",
                                 (job_id,))
                before = self.snapshot()
                historical = self.read(result["session"])
                self.assertEqual(historical["messages"][-1]["content"], "")
                self.assertFalse(historical["messages"][-1].get("response_recovered", False))
                self.assertEqual(historical["messages"][-1]["actions"], [])
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.calls), 1)

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
