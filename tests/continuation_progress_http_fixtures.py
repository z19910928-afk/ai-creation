#!/usr/bin/env python3
"""多轮字数预算续写的运行中进度：真实 HTTP 回归。

多轮路径刻意不逐轮落 chunk（chunk seq 每轮从 1 编号会撞号，合并正文只在
``finish_ai_job`` 落一次），所以 3–6 轮期间 SSE 一个 delta 都没有。运行期间
唯一可靠的活信号是 ``ai_run.round`` 事件，``ai_job_public`` 的 ``progress``
字段就从它投影出来。这个 fixture 覆盖四件事：

1. 单次路径（不传预算）的 job 在排队/成功时 ``progress`` 都是 None；
2. 多轮跑到第 2 轮时 ``/api/ai/jobs/{id}`` 能读到 ``progress``，
   轮次 >= 1、字数 > 0、``max_rounds`` 来自建单冻结的预算；
3. ``/api/ai/queue`` 列表里同一 job 也带 ``progress``，且整页只发一次
   ``event_log`` 批查询（禁止 N+1）；
4. 没有任何 round 事件时 ``progress`` 为 None，不抛异常；
   结束之后即使事件还在也不返回运行中进度。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MOYU_AI_BASE_URL"] = ""
os.environ["MOYU_AI_API_KEY"] = ""
os.environ["MOYU_AI_MODEL"] = ""
os.environ["MOYU_AI_DAILY_QUOTA"] = "0"
from backend import server
from backend import moyu_settings
from backend import moyu_output

ROUND_MATERIAL = re.compile(r'"continuation_round":\{"round_index":(\d+)')
# 第 2 轮起 request_id 是 "{ai_run_id}-r{n}"，假 provider 靠它判断"这是后续轮"。
LATER_ROUND = re.compile(r"-r\d+$")


def round_text(index: int) -> str:
    """第 index 轮的正文：固定 200 字，带轮次标记便于断言。"""
    return f"第{index}段。" + "文" * 196


def requested_round(payload: dict) -> int:
    """从 messages 里读本轮轮次；第 1 轮是冻结请求，读不到就按 1 处理。"""
    for message in payload.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            found = ROUND_MATERIAL.search(content)
            if found:
                return int(found.group(1))
    return 1


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    calls: list[dict] = []
    lock = threading.Lock()
    release = threading.Event()

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.loads(raw)
        case = self.path.split("/")[1]
        request_id = self.headers.get("X-Client-Request-Id") or ""
        with self.lock:
            self.calls.append({"case": case, "payload": payload, "request_id": request_id})
        if case == "gate" and LATER_ROUND.search(request_id):
            # 只挂后续轮：第 1 轮照常返回，于是 round 事件先落库，
            # 测试就在"第 2 轮正在生成"这个瞬间读进度。
            type(self).release.wait(10)
        text = round_text(requested_round(payload))
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            frame = {"model": "progress-fixture", "choices": [
                {"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]}
            self.wfile.write(("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode())
            self.wfile.write((
                "data: " + json.dumps({"choices": [], "usage": {
                    "prompt_tokens": 11, "completion_tokens": 23, "total_tokens": 34}}) + "\n\n"
            ).encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args: object) -> None:
        return


class ContinuationProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.provider.daemon_threads = True
        cls.provider_thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.provider_thread.start()
        cls.base = f"http://127.0.0.1:{cls.provider.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.provider_thread.join(timeout=2)
        if server._AI_EXECUTOR is not None:
            server._AI_EXECUTOR.shutdown(wait=True)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="moyu-continuation-progress-")
        self.root = Path(self.tmp.name)
        server.DB_PATH = self.root / "book.sqlite3"
        os.environ["MOYU_CREDENTIALS_DIR"] = str(self.root / "private-keys")
        server.init_db()
        with server.connect() as conn:
            server.ensure_seed(conn)
            self.chapter_id = conn.execute(
                "SELECT id FROM document_node WHERE kind='chapter' ORDER BY rowid LIMIT 1").fetchone()[0]
        self.project = server.PROJECT_ID
        self.branch = server.BRANCH_ID
        Handler.calls = []
        Handler.release = threading.Event()

    def tearDown(self) -> None:
        Handler.release.set()
        if hasattr(self, "api"):
            self.api.shutdown()
            self.api.server_close()
            self.api_thread.join(timeout=2)
        self.tmp.cleanup()

    # ---- HTTP / provider helpers -------------------------------------------------

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        if not hasattr(self, "api"):
            class AppHandler(server.Handler):
                def log_message(self, *_args: object) -> None:
                    return

            self.api = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
            self.api.daemon_threads = True
            self.api_thread = threading.Thread(target=self.api.serve_forever, daemon=True)
            self.api_thread.start()
        raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.api.server_port}" + path, data=raw, method=method,
            headers={"Content-Type": "application/json", "X-Moyu-Project": self.project},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def settings(self, case: str) -> None:
        model = "writer-fixture"
        with server.connect() as conn:
            moyu_settings.save_settings(conn, self.project, {
                "base_url": self.base + "/" + case, "model": model, "api_key": "fixture-credential",
                "models": {"continue": model}, "styles": {"tone": "克制"},
                "parameters": {"temperature": 0, "max_tokens": 3000}, "daily_limit": 0,
            })

    def calls_of(self, case: str) -> list[dict]:
        with Handler.lock:
            return [dict(call) for call in Handler.calls if call["case"] == case]

    def enqueue(self, *, budget: dict | None = None, defer: bool = True) -> tuple[int, dict]:
        body: dict = {"task": "continue", "defer": defer}
        if budget is not None:
            body["budgeted_continuation"] = budget
        return self.request("POST", "/api/ai/queue", body)

    def job(self, job_id: str) -> dict:
        status, detail = self.request("GET", "/api/ai/jobs/" + job_id)
        self.assertEqual(status, 200, detail)
        return detail

    def wait_job(self, job_id: str) -> dict:
        for _ in range(400):
            detail = self.job(job_id)
            if detail["status"] not in {"queued", "running"}:
                return detail
            time.sleep(0.01)
        self.fail("queued HTTP request never completed")

    def wait_calls(self, case: str, count: int) -> None:
        for _ in range(400):
            if len(self.calls_of(case)) >= count:
                return
            time.sleep(0.01)
        self.fail(f"等待第 {count} 次 provider 调用超时")

    def wait_progress(self, job_id: str) -> dict:
        for _ in range(400):
            detail = self.job(job_id)
            if detail.get("progress"):
                return detail
            time.sleep(0.01)
        self.fail("运行中的多轮 job 始终没有 progress")

    def run_job(self, job_id: str) -> dict:
        status, processed = self.request("POST", f"/api/ai/jobs/{job_id}/process", {})
        self.assertEqual(status, 200, processed)
        return processed

    def round_events(self, run_id: str) -> list[dict]:
        with server.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT payload_json,created_at FROM event_log WHERE aggregate_id=? AND event_type=? "
                "ORDER BY rowid", (run_id, server.BUDGETED_CONTINUATION_EVENT))]

    def traced_queue(self) -> tuple[dict, int]:
        """取一次队列列表，同时数本次请求里发了多少次 event_log 批查询。"""
        queries: list[str] = []
        original = server.connect

        def traced_connect(*args: object, **kwargs: object):
            conn = original(*args, **kwargs)
            conn.set_trace_callback(queries.append)
            return conn

        server.connect = traced_connect
        try:
            status, payload = self.request("GET", "/api/ai/queue?limit=200")
        finally:
            server.connect = original
        self.assertEqual(status, 200, payload)
        batched = sum(1 for query in queries if "aggregate_id IN (" in query)
        return payload, batched

    # ---- 1. 单次路径没有进度 -----------------------------------------------------

    def test_single_path_job_never_exposes_progress(self) -> None:
        self.settings("plain")
        status, queued = self.enqueue(defer=True)
        self.assertEqual(status, 202, queued)
        detail = self.job(queued["id"])
        self.assertIn("progress", detail, "运行中/排队的 job 必须带 progress 键")
        self.assertEqual(detail["status"], "queued")
        self.assertIsNone(detail["progress"], "没有 round 事件时必须为 None")
        self.assertEqual(self.round_events(queued["ai_run_id"]), [])
        self.run_job(queued["id"])
        done = self.wait_job(queued["id"])
        self.assertEqual(done["status"], "success", done)
        self.assertIsNone(done["progress"], "单次路径不产生多轮进度")
        self.assertEqual(self.round_events(queued["ai_run_id"]), [])
        self.assertEqual(len(self.calls_of("plain")), 1, "默认路径仍然只调一次模型")

    # ---- 2. 多轮运行中能读到进度 -------------------------------------------------

    def test_running_budgeted_job_exposes_round_progress(self) -> None:
        self.settings("gate")
        status, queued = self.enqueue(budget={"target_characters": 600, "max_rounds": 3}, defer=False)
        self.assertEqual(status, 202, queued)
        self.wait_calls("gate", 2)
        detail = self.job(queued["id"])
        self.assertEqual(detail["status"], "running", detail)
        progress = detail["progress"]
        self.assertIsNotNone(progress, "第 2 轮正在生成时必须能读到进度")
        self.assertEqual(progress["round_index"], 1, "第 1 轮的 round 事件已落库")
        self.assertEqual(progress["characters"], moyu_output.count_characters(round_text(1)))
        self.assertEqual(progress["characters"], 200)
        self.assertEqual(progress["target_characters"], 600)
        self.assertEqual(progress["remaining_characters"], 400)
        self.assertEqual(progress["max_rounds"], 3, "max_rounds 必须来自建单时冻结的预算")
        self.assertTrue(progress["updated_at"])
        Handler.release.set()
        done = self.wait_job(queued["id"])
        self.assertEqual(done["status"], "success", done)
        self.assertIsNone(done["progress"], "结束后不再返回运行中进度")

    # ---- 3. 列表带进度且只批查一次 -----------------------------------------------

    def test_queue_list_carries_progress_and_batches_queries(self) -> None:
        self.settings("gate")
        jobs = []
        for _ in range(2):
            status, queued = self.enqueue(budget={"target_characters": 600, "max_rounds": 3}, defer=False)
            self.assertEqual(status, 202, queued)
            jobs.append(queued)
        status, plain = self.enqueue(defer=True)
        self.assertEqual(status, 202, plain)
        payload: dict = {}
        batched = 0
        for _ in range(400):
            payload, batched = self.traced_queue()
            by_id = {item["id"]: item for item in payload["jobs"]}
            if all((by_id.get(job["id"]) or {}).get("progress") for job in jobs):
                break
            time.sleep(0.01)
        else:
            self.fail("队列列表始终没有出现进度")
        by_id = {item["id"]: item for item in payload["jobs"]}
        for job in jobs:
            progress = by_id[job["id"]]["progress"]
            self.assertIsNotNone(progress, "列表里同一 job 必须带 progress")
            self.assertGreaterEqual(progress["round_index"], 1)
            self.assertGreater(progress["characters"], 0)
            self.assertEqual(progress["max_rounds"], 3)
        self.assertIsNone(by_id[plain["id"]]["progress"], "没预算的 job 不能被顺手编一个进度")
        self.assertEqual(batched, 1, "整页只允许一次 event_log 批查询，禁止 N+1")
        Handler.release.set()
        for job in jobs:
            self.assertEqual(self.wait_job(job["id"])["status"], "success")

    # ---- 4. 没有事件 / 已结束都不给进度 ------------------------------------------

    def test_progress_is_none_without_round_events(self) -> None:
        self.settings("rounds")
        status, queued = self.enqueue(budget={"target_characters": 600, "max_rounds": 3}, defer=True)
        self.assertEqual(status, 202, queued)
        detail = self.job(queued["id"])
        self.assertEqual(detail["status"], "queued")
        self.assertIsNone(detail["progress"])
        with server.connect() as conn:
            self.assertEqual(server.continuation_progress(conn, []), {})
            self.assertEqual(server.continuation_progress(conn, ["run_missing"]), {})
        self.run_job(queued["id"])
        done = self.wait_job(queued["id"])
        self.assertEqual(done["status"], "success", done)
        self.assertTrue(self.round_events(queued["ai_run_id"]), "多轮确实写了 round 事件")
        self.assertIsNone(self.job(queued["id"])["progress"], "成功之后事件还在，但不是运行中进度")


if __name__ == "__main__":
    unittest.main(verbosity=2)
