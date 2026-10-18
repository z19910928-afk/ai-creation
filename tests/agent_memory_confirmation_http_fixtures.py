#!/usr/bin/env python3
"""Real HTTP writing-agent memory decisions with disposable books and a local model."""
from __future__ import annotations

from collections import deque
from contextlib import closing
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODEL = "memory-confirmation-local"
BODY = (
    "林岚握住旧门的把手，冰冷的金属让她停了一瞬。\n"
    "门里传来两下轻响，她把油灯放在台阶上，没有立刻推门。\n"
    "窗边的影子随即消失，门缝中落下一张写着日期的船票。\n"
    "她捡起船票，认出了失踪兄长的笔迹。"
)
QUOTE = BODY.splitlines()[0]
CONTINUATION = "她将船票夹进衣袖，沿着屋檐慢慢退到街角。\n\n渡口的钟刚响过一声。"
SOURCES = (
    "backend/server.py", "backend/moyu_agent_service.py", "backend/moyu_writing_agent.py", "backend/moyu_analysis.py",
    "backend/moyu_materials.py", "backend/moyu_workspace.py", "backend/moyu_context.py", "backend/moyu_backups.py",
    "tests/agent_memory_confirmation_http_fixtures.py",
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def serve_application():
    """Delay only the completion notification, after the real analysis was committed."""
    from backend import server
    from backend import moyu_agent_service

    control = Path(os.environ["MOYU_MEMORY_TEST_CONTROL"])
    original = moyu_agent_service.advance_ready

    def advance(api, **kwargs):
        if kwargs.get("analysis_id") and (control / "hold-author-callback").exists():
            (control / "author-decision-committed").touch()
            deadline = time.monotonic() + 20
            while (control / "hold-author-callback").exists():
                if time.monotonic() >= deadline:
                    raise AssertionError("test did not release the author callback")
                time.sleep(0.01)
        job_id = kwargs.get("job_id")
        if job_id and (control / "hold-analysis-callback").exists():
            with closing(api.connect()) as conn:
                job = conn.execute("SELECT task FROM app_ai_job WHERE id=?", (job_id,)).fetchone()
            if job and job["task"] == "chapter_analysis":
                (control / "analysis-callback-ready").write_text(job_id, encoding="ascii")
                deadline = time.monotonic() + 20
                while (control / "hold-analysis-callback").exists():
                    if time.monotonic() >= deadline:
                        raise AssertionError("test did not release the analysis callback")
                    time.sleep(0.01)
        return original(api, **kwargs)

    moyu_agent_service.advance_ready = advance
    server.main()


class LoopbackModel:
    def __init__(self):
        self.expected = deque()
        self.calls = []
        self.errors = []
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    if (self.path != "/v1/chat/completions" or
                            self.client_address[0] != "127.0.0.1" or
                            self.headers.get("Authorization")):
                        raise AssertionError("only unauthenticated loopback requests are allowed")
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    if request.get("model") != MODEL:
                        raise AssertionError("unexpected model")
                    with owner.lock:
                        if not owner.expected:
                            raise AssertionError("unexpected extra model call")
                        task, variant = owner.expected.popleft()
                        owner.calls.append({
                            "task": task, "model": request["model"],
                            "request_sha256": hashlib.sha256(encode(request)).hexdigest(),
                            "authorization_present": bool(self.headers.get("Authorization")),
                        })
                    if task == "continue":
                        text = CONTINUATION
                    elif task == "chapter_analysis":
                        evidence = [{"source_quote": QUOTE}]
                        structured = {
                            "summary": {"text": "林岚在门外发现兄长留下的船票。", "evidence": evidence},
                            "handoff": {"text": "沿船票日期寻找渡口的线索。", "evidence": evidence},
                            "candidates": [{
                                "kind": "event", "name": "旧门前的停顿",
                                "note": "林岚握住旧门把手时停了一瞬。",
                                "subject_name": "林岚", "epistemic_mode": "world_truth",
                                "known_by": [], "source_quote": QUOTE,
                            }],
                        }
                        if variant == "empty":
                            structured = {
                                "summary": {"text": "", "evidence": []},
                                "handoff": {"text": "", "evidence": []}, "candidates": [],
                            }
                        text = json.dumps(structured, ensure_ascii=False)
                    else:
                        text = json.dumps({
                            "findings": [], "uncertainties": ["本机合成响应不评定文学质量。"],
                        }, ensure_ascii=False)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    chunk = {
                        "model": MODEL,
                        "choices": [{"delta": {"content": text}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 80, "completion_tokens": 80, "total_tokens": 160},
                    }
                    self.wfile.write(b"data: " + encode(chunk) + b"\n\ndata: [DONE]\n\n")
                    self.wfile.flush()
                except Exception as error:
                    with owner.lock:
                        owner.errors.append(str(error))
                    try:
                        self.send_error(500)
                    except OSError:
                        pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(
            target=lambda: self.http.serve_forever(poll_interval=0.05), daemon=True,
        )
        self.thread.start()

    def expect(self, task, *, count=1, variant="normal"):
        with self.lock:
            self.expected.extend((task, variant) for _ in range(count))

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise AssertionError("local model did not stop")


class MemoryConfirmationHTTP(unittest.TestCase):
    observations = []

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="moyu-agent-memory-http-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = self.root / "synthetic-book.sqlite3"
        self.provider = LoopbackModel()
        self.addCleanup(self.provider.close)
        self.opener = build_opener(ProxyHandler({}))
        self.project = "default-project"
        self.app = None
        self.log = None
        self.addCleanup(self.stop_app)
        self.start_app()
        self.chapter_id = self.prepare_book()

    def tearDown(self):
        self.stop_app()
        self.assertEqual(self.provider.errors, [])
        self.assertEqual(list(self.provider.expected), [])
        self.assertTrue(all(not call["authorization_present"] for call in self.provider.calls))
        self.observations.append({
            "method": self._testMethodName, "synthetic_only": True,
            "free_local_provider_calls": len(self.provider.calls), "paid_provider_calls": 0,
            "requests": copy.deepcopy(self.provider.calls),
        })

    def start_app(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        environment = {key: os.environ[key] for key in
                       ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
        environment.update({
            "MOYU_DB": str(self.database), "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(self.port),
            "MOYU_AI_BASE_URL": f"http://127.0.0.1:{self.provider.http.server_port}/v1",
            "MOYU_AI_MODEL": MODEL, "MOYU_AI_API_KEY": "", "MOYU_SECRET_KEY": "",
            "MOYU_AI_DAILY_QUOTA": "100", "MOYU_CREDENTIALS_DIR": str(self.root / "credentials"),
            "MOYU_MEMORY_TEST_CONTROL": str(self.root), "PYTHONUNBUFFERED": "1",
        })
        self.log = (self.root / f"app-{self.port}.log").open("xb")
        self.app = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), "--serve"],
            cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=self.log, stderr=subprocess.STDOUT,
        )

        def healthy():
            try:
                status, value = self.call("GET", "/api/health")
                self.assertEqual(value["db"], str(self.database))
                return status == 200
            except OSError:
                return False

        self.until(healthy, "isolated application startup")

    def stop_app(self):
        if self.app is not None and self.app.poll() is None:
            self.app.terminate()
            try:
                self.app.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.app.kill()
                self.app.wait(timeout=5)
        if self.log is not None:
            self.log.close()
        self.app = None
        self.log = None

    def call(self, method, route, payload=None, *, project=None):
        request = Request(self.base + route, method=method,
                          data=None if payload is None else encode(payload),
                          headers={"Content-Type": "application/json",
                                   "X-Moyu-Project": project or self.project})
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def api(self, method, route, payload=None, *, project=None):
        status, value = self.call(method, route, payload, project=project)
        self.assertTrue(200 <= status < 300, (route, status, value))
        return value

    def until(self, predicate, label):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.app is not None:
                self.assertIsNone(self.app.poll(), f"isolated application exited: {label}")
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        self.fail("timed out: " + label)

    def prepare_book(self):
        state = self.api("GET", "/api/state")
        chapter = state["chapters"][0]
        self.api("PATCH", "/api/chapters/" + chapter["id"], {
            "body": BODY, "status": "进行中", "chapter_goal": "找到船票线索",
            "scene_plan": "在旧门前停顿，发现船票", "pov": "林岚", "recap": "", "next_brief": "",
        })
        self.person = self.api("POST", "/api/records", {
            "type": "人物", "name": "林岚", "description": "寻找失踪兄长的人。",
        })
        return chapter["id"]

    def session(self, identity, *, project=None):
        rows = self.api("GET", "/api/writing-agent?session_id=" + identity,
                        project=project)["sessions"]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def wait_status(self, identity, status):
        def poll():
            row = self.session(identity)
            if row["status"] == "requires_action" and status != "requires_action":
                self.fail(row["error"])
            return row if row["status"] == status else None
        return self.until(poll, status)

    def begin(self, *, mode="inspect", variant="normal", chapter_id=None):
        if mode == "continue":
            self.provider.expect("continue")
        self.provider.expect("chapter_analysis", variant=variant)
        planned = self.api("POST", "/api/writing-agent", {
            "request_id": "memory_" + str(time.monotonic_ns()),
            "chapter_id": chapter_id or self.chapter_id,
            "query": "完成本章正文后，整理有证据的回顾、交接和候选记忆，再做故事审稿。",
            "mode": mode,
        })["session"]
        self.assertEqual(planned["status"], "awaiting_authorization")
        self.api("POST", "/api/writing-agent/" + planned["id"] + "/resume", {
            "action": "authorize", "checkpoint_id": planned["checkpoint_id"],
        })
        return planned

    def waiting(self, **kwargs):
        session = self.begin(**kwargs)
        return self.wait_status(session["id"], "awaiting_memory")

    def analysis(self, session):
        reference = self.session(session["id"])["result_refs"]["chapter_analysis"]["id"]
        reports = self.api("GET", "/api/chapter-analysis?chapter_id=" + session["chapter_id"])["analyses"]
        return next(item for item in reports if item["id"] == reference)

    def decision_payload(self, analysis, *, decision="accepted", **fields):
        payload = {
            "source_revision_id": analysis["source_revision_id"],
            "chapter_hash": analysis["chapter_hash"],
            "decisions": [{"candidate_id": item["id"], "decision": decision}
                          for item in analysis["candidates"] if item["status"] == "candidate"],
        }
        for name in ("summary", "handoff"):
            if analysis[name]["status"] == "pending":
                payload[name + "_decision"] = decision
        return {**payload, **fields}

    def confirm(self, analysis, *, payload=None, decision="accepted", project=None):
        return self.call("POST", "/api/chapter-analysis/" + analysis["id"] + "/confirm",
                         payload or self.decision_payload(analysis, decision=decision),
                         project=project)

    def finish(self, session, *, decision="accepted"):
        analysis = self.analysis(session)
        self.provider.expect("semantic_review")
        status, response = self.confirm(analysis, decision=decision)
        self.assertEqual(status, 200, response)
        current = self.wait_status(session["id"], "review_ready")
        self.assertFalse(current["source_stale"])
        return current

    def query(self, statement, args=()):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(statement, args)]

    def mutate(self, statement, args):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute(statement, args)

    def counts(self):
        return {name: self.query("SELECT COUNT(*) AS n FROM " + name)[0]["n"] for name in (
            "app_ai_job", "ai_run", "app_quota_reservation", "app_chapter_analysis",
            "app_story_review", "document_revision", "candidate_memory",
        )}

    def seals(self, session):
        step = self.query("SELECT plan_json FROM app_agent_session WHERE id=?", (session["id"],))[0]
        plan = json.loads(step["plan_json"])
        job = self.query("SELECT context_json,ai_run_id FROM app_ai_job WHERE id=?",
                         (plan["steps"]["chapter_analysis"]["job_id"],))[0]
        consent = self.query("SELECT consent_json FROM ai_run WHERE id=?", (job["ai_run_id"],))[0]
        saved = self.query("SELECT payload_json FROM app_chapter_analysis WHERE id=?",
                           (self.analysis(session)["id"],))[0]
        item = json.loads(saved["payload_json"])
        return {
            "context": json.loads(job["context_json"]),
            "request_audit": json.loads(consent["consent_json"]).get("request_audit"),
            "material_source": item.get("material_source"),
            "guidance_source": item.get("guidance_source"),
        }

    def test_partial_decisions_wait_then_review_uses_current_confirmed_memory(self):
        session = self.waiting()
        self.assertEqual(session["calls_used"], 1)
        self.assertEqual(len(self.provider.calls), 1)
        analysis = self.analysis(session)
        frozen = self.seals(session)
        payload = self.decision_payload(analysis, decisions=[], handoff_decision=None)
        status, result = self.confirm(analysis, payload=payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.session(session["id"])["status"], "awaiting_memory")
        self.assertEqual(len(self.provider.calls), 1)
        current = self.finish(session)
        self.assertEqual(current["calls_used"], 2)
        self.assertEqual(self.seals(session), frozen)
        stored = self.analysis(session)
        self.assertEqual(stored["status"], "reviewed")
        self.assertTrue(all(item["status"] == "accepted" for item in stored["candidates"]))
        state = self.api("GET", "/api/state")
        chapter = next(item for item in state["chapters"] if item["id"] == self.chapter_id)
        self.assertEqual(chapter["body"], BODY)
        self.assertEqual(chapter["recap"], analysis["summary"]["text"])
        self.assertEqual(chapter["next_brief"], analysis["handoff"]["text"])
        reviews = self.api("GET", "/api/story-reviews?chapter_id=" + self.chapter_id)["reviews"]
        self.assertFalse(reviews[0].get("stale"), reviews[0])
        gate = self.api("POST", "/api/chapters/" + self.chapter_id + "/commit", {"stage": "prewrite"})
        self.assertEqual(gate["status"], "passed", gate)
        before = self.counts()
        status, result = self.confirm(stored)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(self.provider.calls), 2)

    def test_rejecting_all_items_preserves_formal_memory_and_still_reviews(self):
        session = self.waiting()
        before = self.counts()
        current = self.finish(session, decision="rejected")
        self.assertEqual(current["calls_used"], 2)
        self.assertEqual(self.counts()["candidate_memory"], before["candidate_memory"])
        chapter = next(item for item in self.api("GET", "/api/state")["chapters"]
                       if item["id"] == self.chapter_id)
        self.assertEqual((chapter["recap"], chapter["next_brief"]), ("", ""))
        analysis = self.analysis(session)
        self.assertEqual(analysis["summary"]["status"], "rejected")
        self.assertEqual(analysis["handoff"]["status"], "rejected")
        self.assertTrue(all(item["status"] == "rejected" for item in analysis["candidates"]))

    def test_concurrent_confirmation_and_old_checkpoint_do_not_repeat_review(self):
        session = self.waiting()
        analysis = self.analysis(session)
        payload = self.decision_payload(analysis)
        barrier = threading.Barrier(3)
        outcomes, errors = [], []
        self.provider.expect("semantic_review")

        def submit():
            try:
                barrier.wait(timeout=5)
                outcomes.append(self.confirm(analysis, payload=payload))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual([status for status, _ in outcomes], [200, 200], outcomes)
        completed = self.wait_status(session["id"], "review_ready")
        before = self.counts()
        repeated = self.api("POST", "/api/writing-agent/" + session["id"] + "/resume", {
            "action": "advance", "checkpoint_id": session["checkpoint_id"],
        })
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["session"]["id"], completed["id"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(sum(row["task"] == "semantic_review" for row in self.provider.calls), 1)

    def test_author_confirmation_before_worker_callback_preserves_original_seals(self):
        hold = self.root / "hold-analysis-callback"
        hold.touch()
        session = self.begin()
        self.until(lambda: (self.root / "analysis-callback-ready").exists(), "delayed analysis callback")
        current = self.session(session["id"])
        self.assertEqual(current["status"], "awaiting_job")
        frozen = self.seals(current)
        self.provider.expect("semantic_review")
        status, response = self.confirm(self.analysis(current))
        self.assertEqual(status, 200, response)
        hold.unlink()
        completed = self.wait_status(session["id"], "review_ready")
        self.assertEqual(completed["calls_used"], 2)
        self.assertEqual(self.seals(completed), frozen)
        self.assertEqual(len(self.provider.calls), 2)

    def test_restart_while_awaiting_memory_never_regenerates_analysis(self):
        session = self.waiting()
        before = self.counts()
        self.stop_app()
        self.start_app()
        resumed = self.session(session["id"])
        self.assertEqual(resumed["status"], "awaiting_memory")
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(self.provider.calls), 1)
        completed = self.finish(resumed)
        self.assertEqual(completed["calls_used"], 2)
        self.assertEqual(len(self.provider.calls), 2)

    def test_restart_after_committed_decision_before_notification_enqueues_review_once(self):
        session = self.waiting()
        analysis = self.analysis(session)
        hold = self.root / "hold-author-callback"
        hold.touch()
        outcomes = []

        def submit():
            try:
                outcomes.append(self.confirm(analysis))
            except OSError:
                outcomes.append("connection closed with the terminated process")

        request = threading.Thread(target=submit)
        request.start()
        self.until(lambda: (self.root / "author-decision-committed").exists(),
                   "committed decision before agent notification")
        stored = json.loads(self.query("SELECT payload_json FROM app_chapter_analysis WHERE id=?",
                                       (analysis["id"],))[0]["payload_json"])
        self.assertEqual(stored["status"], "reviewed")
        self.assertEqual(self.session(session["id"])["status"], "awaiting_memory")
        self.assertEqual(len(self.provider.calls), 1)
        self.stop_app()
        request.join(timeout=10)
        self.assertFalse(request.is_alive())
        self.assertEqual(outcomes, ["connection closed with the terminated process"])
        hold.unlink()
        self.provider.expect("semantic_review")
        self.start_app()
        completed = self.wait_status(session["id"], "review_ready")
        self.assertEqual(completed["calls_used"], 2)
        self.assertEqual(self.counts()["app_ai_job"], 2)
        self.assertEqual(len(self.provider.calls), 2)

    def test_real_continuation_adoption_checks_the_adopted_revision(self):
        planned = self.begin(mode="continue")
        session = self.wait_status(planned["id"], "awaiting_adoption")
        job = session["current_job"]
        state = self.api("GET", "/api/state")
        self.api("POST", "/api/ai/runs/" + job["ai_run_id"] + "/apply", {
            "replacement": "\n\n" + job["result"]["text"], "decision": "accepted",
            "range": job["source"]["range"], "expected_head": state["head_revision"],
        })
        waiting = self.wait_status(session["id"], "awaiting_memory")
        analysis = self.analysis(waiting)
        self.assertNotEqual(analysis["source_revision_id"], job["source"]["source_revision_id"])
        self.assertEqual(len(self.provider.calls), 2)
        complete = self.finish(waiting)
        self.assertEqual(complete["calls_used"], 3)
        self.assertEqual(len(self.provider.calls), 3)
        chapter = next(item for item in self.api("GET", "/api/state")["chapters"]
                       if item["id"] == self.chapter_id)
        self.assertEqual(chapter["body"], BODY + "\n\n" + CONTINUATION)

    def test_shared_analysis_with_original_run_id_can_advance_each_authorized_task_once(self):
        first = self.waiting()
        second = self.waiting()
        left, right = self.analysis(first), self.analysis(second)
        self.assertEqual(left["id"], right["id"])
        self.assertEqual(left["run_id"], right["run_id"])
        self.assertNotEqual(first["current_job"]["ai_run_id"], second["current_job"]["ai_run_id"])
        self.assertEqual(self.seals(first)["material_source"], self.seals(second)["material_source"])
        self.provider.expect("semantic_review", count=2)
        status, response = self.confirm(left)
        self.assertEqual(status, 200, response)
        self.wait_status(first["id"], "review_ready")
        self.wait_status(second["id"], "review_ready")
        self.assertEqual(len(self.provider.calls), 4)
        self.assertEqual(sum(call["task"] == "semantic_review" for call in self.provider.calls), 2)
        self.assertEqual(self.counts()["app_ai_job"], 4)

    def assert_change_blocks(self, change):
        session = self.waiting()
        analysis = self.analysis(session)
        frozen = self.seals(session)
        if change == "body":
            self.api("PATCH", "/api/chapters/" + self.chapter_id, {"body": BODY + "\n作者改过这一句。"})
        elif change == "record":
            self.api("PATCH", "/api/records/" + self.person["id"], {"description": "作者重写了人物背景。"})
        elif change == "plan":
            self.api("PATCH", "/api/chapters/" + self.chapter_id, {"scene_plan": "改为关门离开。"})
        elif change == "blueprint":
            saved = self.api("GET", "/api/blueprint")
            blueprint = copy.deepcopy(saved["blueprint"])
            blueprint["contract"] = "保留兄长秘密，不能提前揭示。"
            self.api("PUT", "/api/blueprint", {"expected_version": saved["version"], "blueprint": blueprint})
        elif change == "model":
            self.api("PUT", "/api/ai/settings", {
                "base_url": f"http://127.0.0.1:{self.provider.http.server_port}/v1",
                "model": "memory-confirmation-changed-local", "api_key": "", "daily_limit": 100,
            })
        before = self.counts()
        if change != "model":
            self.assertTrue(self.session(session["id"])["source_stale"])
            status, result = self.confirm(analysis)
            self.assertEqual(status, 409, result)
            self.assertEqual(self.counts(), before)
            self.api("POST", "/api/writing-agent/" + session["id"] + "/resume", {
                "action": "advance", "checkpoint_id": session["checkpoint_id"],
            })
        else:
            status, result = self.confirm(analysis)
            self.assertEqual(status, 200, result)
        current = self.wait_status(session["id"], "requires_action")
        self.assertTrue(current["error"])
        self.assertEqual(self.counts()["app_ai_job"], before["app_ai_job"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.seals(session), frozen)

    def test_changed_body_cannot_start_review(self):
        self.assert_change_blocks("body")

    def test_changed_character_material_cannot_start_review(self):
        self.assert_change_blocks("record")

    def test_changed_chapter_plan_cannot_start_review(self):
        self.assert_change_blocks("plan")

    def test_changed_blueprint_cannot_start_review(self):
        self.assert_change_blocks("blueprint")

    def test_changed_model_cannot_start_review(self):
        self.assert_change_blocks("model")

    def test_empty_analysis_still_waits_for_explicit_author_completion(self):
        session = self.waiting(variant="empty")
        analysis = self.analysis(session)
        self.assertEqual(analysis["summary"]["status"], "empty")
        self.assertEqual(analysis["handoff"]["status"], "empty")
        self.assertEqual(analysis["candidates"], [])
        self.assertEqual(len(self.provider.calls), 1)
        self.finish(session)
        self.assertEqual(len(self.provider.calls), 2)

    def test_cancelled_memory_wait_never_starts_review_after_author_decision(self):
        session = self.waiting()
        analysis = self.analysis(session)
        canceled = self.api("POST", "/api/writing-agent/" + session["id"] + "/resume", {
            "action": "cancel", "checkpoint_id": session["checkpoint_id"],
        })["session"]
        self.assertEqual(canceled["status"], "canceled")
        before = self.counts()
        status, result = self.confirm(analysis)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.session(session["id"])["status"], "canceled")
        self.assertEqual(self.counts()["app_ai_job"], before["app_ai_job"])
        self.assertEqual(len(self.provider.calls), 1)

    def test_wrong_analysis_reference_cannot_resume_another_chapter(self):
        first = self.waiting()
        other = self.api("POST", "/api/chapters", {
            "title": "第二章 隔离来源", "body": BODY,
            "chapter_goal": "另一章的目标", "scene_plan": "另一章的场景",
        })
        chapter_id = other.get("id") or other.get("chapter", {}).get("id")
        self.assertTrue(chapter_id, other)
        second = self.waiting(chapter_id=chapter_id)
        wrong = self.analysis(second)
        plan = json.loads(self.query("SELECT plan_json FROM app_agent_session WHERE id=?",
                                     (first["id"],))[0]["plan_json"])
        job_id = plan["steps"]["chapter_analysis"]["job_id"]
        result = json.loads(self.query("SELECT result_json FROM app_ai_job WHERE id=?", (job_id,))[0]["result_json"])
        result["analysis"]["id"] = wrong["id"]
        self.mutate("UPDATE app_ai_job SET result_json=? WHERE id=?", (json.dumps(result), job_id))
        before = self.counts()
        self.api("POST", "/api/writing-agent/" + first["id"] + "/resume", {
            "action": "advance", "checkpoint_id": first["checkpoint_id"],
        })
        stopped = self.wait_status(first["id"], "requires_action")
        self.assertTrue(stopped["error"])
        self.assertEqual(self.session(second["id"])["status"], "awaiting_memory")
        self.assertEqual(self.counts()["app_ai_job"], before["app_ai_job"])
        self.assertEqual(len(self.provider.calls), 2)

    def assert_missing_analysis_seal_blocks(self, name):
        session = self.waiting()
        analysis = self.analysis(session)
        raw = json.loads(self.query("SELECT payload_json FROM app_chapter_analysis WHERE id=?",
                                    (analysis["id"],))[0]["payload_json"])
        raw.pop(name)
        self.mutate("UPDATE app_chapter_analysis SET payload_json=? WHERE id=?",
                    (json.dumps(raw), analysis["id"]))
        before = self.counts()
        self.api("POST", "/api/writing-agent/" + session["id"] + "/resume", {
            "action": "advance", "checkpoint_id": session["checkpoint_id"],
        })
        self.wait_status(session["id"], "requires_action")
        self.assertEqual(self.counts()["app_ai_job"], before["app_ai_job"])
        self.assertEqual(len(self.provider.calls), 1)

    def test_missing_original_analysis_seal_cannot_be_replaced_by_current_materials(self):
        self.assert_missing_analysis_seal_blocks("material_source")

    def test_missing_original_guidance_seal_cannot_be_replaced_by_current_plan(self):
        self.assert_missing_analysis_seal_blocks("guidance_source")

    def test_tampered_request_audit_cannot_authorize_a_followup_without_references(self):
        session = self.waiting()
        analysis = self.analysis(session)
        run_id = session["current_job"]["ai_run_id"]
        saved = self.query("SELECT consent_json FROM ai_run WHERE id=?", (run_id,))[0]
        consent = json.loads(saved["consent_json"])
        consent["request_audit"]["payload"]["model"] = "tampered-local-model"
        consent["request_audit"]["request_sha256"] = "0" * 64
        self.mutate("UPDATE ai_run SET consent_json=? WHERE id=?", (json.dumps(consent), run_id))
        before = self.counts()
        status, result = self.confirm(analysis)
        self.assertEqual(status, 200, result)
        self.wait_status(session["id"], "requires_action")
        self.assertEqual(self.counts()["app_ai_job"], before["app_ai_job"])
        self.assertEqual(len(self.provider.calls), 1)

    def test_foreign_project_decision_cannot_wake_waiting_task(self):
        session = self.waiting()
        analysis = self.analysis(session)
        other = self.api("POST", "/api/projects", {"title": "另一隔离作品"})["project"]["id"]
        before = self.counts()
        status, result = self.confirm(analysis, project=other)
        self.assertIn(status, (400, 404), result)
        self.assertEqual(self.session(session["id"])["status"], "awaiting_memory")
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(self.provider.calls), 1)

    def test_restored_waiting_task_remains_read_only_across_restart(self):
        session = self.waiting()
        artifact = self.api("POST", "/api/backups", {})
        restored = self.api("POST", "/api/backups/" + artifact["id"] + "/restore", {"confirm": True})
        project = restored["project"]["id"]
        rows = self.api("GET", "/api/writing-agent", project=project)["sessions"]
        self.assertEqual(len(rows), 1, rows)
        restored_session = rows[0]
        self.assertTrue(restored_session["restored_read_only"])
        self.assertEqual(restored_session["checkpoint_id"], "")
        before = self.counts()
        status, result = self.call("POST", "/api/writing-agent/" + restored_session["id"] + "/resume", {
            "action": "advance", "checkpoint_id": session["checkpoint_id"],
        }, project=project)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.counts(), before)
        self.stop_app()
        self.start_app()
        current = self.session(restored_session["id"], project=project)
        self.assertTrue(current["restored_read_only"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(self.provider.calls), 1)


def main():
    before = fingerprints()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MemoryConfirmationHTTP)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful() and before == after,
        "source_unchanged": before == after, "checks": result.testsRun,
        "methods": MemoryConfirmationHTTP.observations,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "free_local_provider_calls": sum(item["free_local_provider_calls"]
                                         for item in MemoryConfirmationHTTP.observations),
        "paid_provider_calls": 0, "synthetic_only": True,
        "scope": "Real isolated HTTP application subprocesses, temporary SQLite and unauthenticated loopback SSE model.",
        "failures": [str(test) for test, _ in result.failures],
        "errors": [str(test) for test, _ in result.errors],
    }
    output = os.environ.get("MOYU_AGENT_MEMORY_REPORT")
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps({key: report[key] for key in (
        "passed", "source_unchanged", "checks", "free_local_provider_calls", "paid_provider_calls",
    )}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        serve_application()
    else:
        raise SystemExit(main())
