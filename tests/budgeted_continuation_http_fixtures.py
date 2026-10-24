#!/usr/bin/env python3
"""多轮字数预算续写的真实 HTTP 回归：假 provider 记录每一轮收到的请求。

覆盖的六件事：

1. 不传预算字段时，默认路径与现在完全一致：只调一次 provider，chunk 从 1 起连续。
2. 传预算时真的多轮生成，正文字数落在目标区间，合并正文 == 逐轮正文拼接。
3. 每轮提示词不同：第 2 轮必须带上第 1 轮正文与「还差约 N 字」，防同一提示词跑 N 遍。
4. 取消能中断，且取消后不再派发下一轮。
5. 额度耗尽时如实停在 budget_exhausted，并暴露真实调用次数。
6. app_ai_chunk 不撞号，正文 hash 可复现。
7. 从断点续写：重试沿用上一次已落盘的正文（第 0 轮），正文不裁剪、账目不重复计。
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
from backend import moyu_continuation
from backend import moyu_settings
from backend import moyu_output

ROUND_MATERIAL = re.compile(r'"continuation_round":\{"round_index":(\d+)')
ROUND_TEXT_SUFFIX = "文"
# 停滞轮正文：固定 30 字，低于单轮有效增量下限（引擎默认 50 字）。
STALL_TEXT = "第2段。" + ROUND_TEXT_SUFFIX * 26


def round_text(index: int) -> str:
    """第 index 轮的正文：固定 200 字，且带轮次标记，便于断言拼接顺序。"""
    return f"第{index}段。" + ROUND_TEXT_SUFFIX * 196


def requested_round(payload: dict) -> int:
    """从 provider 收到的 messages 里读出本轮轮次；读不到就是第 1 轮（冻结请求）。"""
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
    # boom / trunc 只在第一次遇到时失败/截断：同一个 case 既能造出可重试的失败，
    # 又能在「重试」时给出成功。重试会校验模型身份，不能中途改 settings 换 case。
    failed: set[str] = set()

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.loads(raw)
        case = self.path.split("/")[1]
        with self.lock:
            self.calls.append({
                "case": case,
                "path": self.path,
                "payload": payload,
                "request_id": self.headers.get("X-Client-Request-Id"),
            })
        index = requested_round(payload)
        if case == "gate":
            # 挂住这一轮，让测试有机会在「正在生成」时请求取消。
            type(self).release.wait(5)
        elif case == "gate2" and index == 2:
            # 只挂第 2 轮：这一轮确实已经发出去，测试才能证明失败/取消轮也进账。
            type(self).release.wait(5)
        elif case == "gate3" and index == 2:
            # 先上报用量再挂住：真实 provider 的 usage 可以先于正文出现。
            # 取消落在这里时用量已知，运行结束 billing_state=usage_reported——
            # 只有这种「停得住、账又算得清」的中断才允许断点续写；
            # 计费不明的失败仍必须被重试守卫挡住（见 unknown_billing 用例）。
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(("data: " + json.dumps({"choices": [], "usage": {
                    "prompt_tokens": 11, "completion_tokens": 23, "total_tokens": 34}}) + "\n\n").encode())
                self.wfile.flush()
                type(self).release.wait(5)
                self.wfile.write(("data: " + json.dumps({"model": "round-fixture", "choices": [
                    {"index": 0, "delta": {"content": round_text(index)},
                     "finish_reason": "stop"}]}, ensure_ascii=False) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        elif case == "boom" and index == 2 and "boom" not in type(self).failed:
            # 第 2 轮服务端报错：请求已经发出（可能已计费），报告必须照实计数。
            type(self).failed.add("boom")
            self.fail_with(500, "provider exploded")
            return
        finish = "stop"
        if case == "trunc" and "trunc" not in type(self).failed:
            # 截断候选：safe_to_apply=False，任务失败但用量已知，
            # 因此是可重试的失败——用来验证「没有种子就逐字重放冻结请求」。
            type(self).failed.add("trunc")
            finish = "length"
        if case == "single":
            text = "先到正文后到正文"
        elif case == "stall" and index == 2:
            # 停滞场景：第 2 轮只写 30 字，逼引擎进入 v1.2 恢复期并给出最少字数配额。
            text = STALL_TEXT
        else:
            text = round_text(index)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if case == "single":
                frames = [
                    {"model": "round-fixture", "choices": [
                        {"index": 0, "delta": {"content": "先到正文"}, "finish_reason": None}]},
                    {"model": "round-fixture", "choices": [
                        {"index": 0, "delta": {"content": "后到正文"}, "finish_reason": "stop"}]},
                ]
            else:
                frames = [
                    {"model": "round-fixture", "choices": [
                        {"index": 0, "delta": {"content": text}, "finish_reason": finish}]},
                ]
            for frame in frames:
                self.wfile.write(("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
            self.wfile.write((
                "data: " + json.dumps({"choices": [], "usage": {
                    "prompt_tokens": 11, "completion_tokens": 23, "total_tokens": 34}}) + "\n\n"
            ).encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def fail_with(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message}}, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args: object) -> None:
        return


class BudgetedContinuationTests(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory(prefix="moyu-budgeted-continuation-")
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
        Handler.failed = set()
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

    def settings(self, case: str, *, limit: int = 0) -> None:
        model = "writer-fixture"
        with server.connect() as conn:
            moyu_settings.save_settings(conn, self.project, {
                "base_url": self.base + "/" + case, "model": model, "api_key": "fixture-credential",
                "models": {"continue": model}, "styles": {"tone": "克制"},
                "parameters": {"temperature": 0, "max_tokens": 3000}, "daily_limit": limit,
            })

    def calls_of(self, case: str) -> list[dict]:
        with Handler.lock:
            return [dict(call) for call in Handler.calls if call["case"] == case]

    def user_message(self, call: dict) -> str:
        return call["payload"]["messages"][-1]["content"]

    def wait_job(self, job_id: str) -> dict:
        for _ in range(400):
            status, detail = self.request("GET", "/api/ai/jobs/" + job_id)
            self.assertEqual(status, 200, detail)
            if detail["status"] not in {"queued", "running"}:
                return detail
            time.sleep(0.01)
        self.fail("queued HTTP request never completed")

    def run_job(self, job_id: str) -> dict:
        status, processed = self.request("POST", f"/api/ai/jobs/{job_id}/process", {})
        self.assertEqual(status, 200, processed)
        return processed

    def enqueue(self, *, task: str = "continue", budget: dict | None = None,
                defer: bool = True) -> tuple[int, dict]:
        body: dict = {"task": task, "defer": defer}
        if budget is not None:
            body["budgeted_continuation"] = budget
        return self.request("POST", "/api/ai/queue", body)

    def chunk_rows(self, run_id: str) -> list[dict]:
        with server.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT seq,text,payload_hash,cumulative_hash FROM app_ai_chunk "
                "WHERE ai_run_id=? ORDER BY seq", (run_id,))]

    def run_row(self, run_id: str) -> dict:
        with server.connect() as conn:
            return dict(conn.execute(
                "SELECT status,output_hash,prompt_hash,token_json,consent_json,mode "
                "FROM ai_run WHERE id=?",
                (run_id,)).fetchone())

    def assert_chunks_form_one_segment(self, run_id: str, body: str) -> None:
        rows = self.chunk_rows(run_id)
        self.assertTrue(rows, "应至少写入一段 chunk")
        self.assertEqual([row["seq"] for row in rows], list(range(1, len(rows) + 1)),
                         "chunk 的 seq 必须从 1 起连续，否则说明多轮撞号")
        self.assertEqual("".join(row["text"] for row in rows), body,
                         "chunk 拼接必须逐字等于最终正文")

    # ---- 1. 默认路径不变 ---------------------------------------------------------

    def test_continue_without_budget_calls_provider_exactly_once(self) -> None:
        self.settings("single")
        status, job = self.enqueue()
        self.assertEqual(status, 202, job)
        self.assertNotIn("budgeted_continuation", job, "没传预算就不该出现多轮字段")
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        self.assertNotIn("budgeted_continuation", processed["result"])
        calls = self.calls_of("single")
        self.assertEqual(len(calls), 1, "没要求多轮预算就必须只调一次模型")
        body = processed["result"]["text"]
        self.assertEqual(body, "先到正文后到正文")
        self.assert_chunks_form_one_segment(job["ai_run_id"], body)
        run = self.run_row(job["ai_run_id"])
        self.assertEqual(run["output_hash"], server.hash_text(body))
        self.assertNotIn("budgeted_continuation_report", json.loads(run["consent_json"]))

    # ---- 2. 多轮生成 + 拼接 + 目标区间 -------------------------------------------

    def test_budgeted_continuation_concatenates_rounds_into_target_range(self) -> None:
        self.settings("rounds")
        status, job = self.enqueue(budget={"target_characters": 1000, "max_rounds": 6})
        self.assertEqual(status, 202, job)
        self.assertEqual(job["budgeted_continuation"]["target_characters"], 1000)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        result = processed["result"]
        report = result["budgeted_continuation"]
        calls = self.calls_of("rounds")
        self.assertEqual(report["dispatched_calls"], len(calls), "报告的调用次数必须等于真实调用次数")
        self.assertEqual([call["payload"]["messages"][0]["content"] for call in calls],
                         [calls[0]["payload"]["messages"][0]["content"]] * len(calls))
        expected = "\n\n".join(round_text(index) for index in range(1, len(calls) + 1))
        self.assertEqual(result["text"], expected, "最终正文必须是逐轮拼接，不能只留最后一轮")
        self.assertEqual(report["stop_reason"], "target_reached")
        self.assertEqual(report["rounds"], len(calls))
        self.assertTrue(report["target_reached"])
        self.assertTrue(report["within_tolerance"])
        self.assertEqual(report["characters"], moyu_output.count_characters(result["text"]))
        self.assertGreaterEqual(report["characters"], report["target_range"]["minimum"])
        self.assertLessEqual(report["characters"], report["target_range"]["maximum"])
        self.assertEqual(len(report["round_metrics"]), len(calls))
        self.assertEqual([item["cumulative_characters"] for item in report["round_metrics"]],
                         [200 * index for index in range(1, len(calls) + 1)])
        self.assertFalse(result["truncated"])
        self.assertTrue(result["safe_to_apply"])
        # 记账：每轮都是真实调用，用量必须累加而不是只记一次。
        run = self.run_row(job["ai_run_id"])
        tokens = json.loads(run["token_json"])
        self.assertEqual(tokens["usage"]["total_tokens"], 34 * len(calls))
        self.assertEqual(tokens["usage_state"], "reported")
        consent = json.loads(run["consent_json"])
        self.assertEqual(consent["budgeted_continuation_report"]["dispatched_calls"], len(calls))
        self.assertEqual(consent["billing_state"], "usage_reported")
        self.assert_chunks_form_one_segment(job["ai_run_id"], result["text"])
        self.assertEqual(run["output_hash"], server.hash_text(result["text"]))

    # ---- 3. 每轮提示词不同 -------------------------------------------------------

    def test_each_round_prompt_carries_previous_text_and_remaining(self) -> None:
        self.settings("rounds")
        status, job = self.enqueue(budget={"target_characters": 600, "max_rounds": 6})
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        calls = self.calls_of("rounds")
        self.assertEqual(len(calls), 3, "600 字目标按每轮 200 字应当正好三轮")
        first, second, third = (self.user_message(call) for call in calls)
        self.assertNotIn("continuation_round", first, "第 1 轮沿用冻结请求，不能提前注入多轮材料")
        self.assertNotEqual(first, second, "第 2 轮不能重放第 1 轮的同一提示词")
        self.assertIn(round_text(1), second, "第 2 轮必须带上第 1 轮正文")
        self.assertIn("还差约 400 字", second)
        self.assertIn("第 2 轮", second)
        self.assertIn('"previous_text"', second)
        self.assertIn(round_text(2), third)
        self.assertIn("还差约 200 字", third)
        self.assertEqual(calls[0]["request_id"], job["ai_run_id"], "第 1 轮沿用本次 run 的请求 id")
        self.assertNotEqual(calls[1]["request_id"], calls[0]["request_id"])
        self.assertTrue(all(call["payload"]["stream"] for call in calls))

    # ---- 4. 取消能中断 -----------------------------------------------------------

    def test_cancel_stops_before_next_round(self) -> None:
        self.settings("gate")
        status, job = self.enqueue(budget={"target_characters": 1000, "max_rounds": 6}, defer=False)
        self.assertEqual(status, 202, job)
        for _ in range(400):
            if self.calls_of("gate"):
                break
            time.sleep(0.01)
        self.assertEqual(len(self.calls_of("gate")), 1, "第 1 轮应当已经派发出去")
        status, canceled = self.request("POST", f"/api/ai/jobs/{job['id']}/cancel", {})
        self.assertEqual(status, 202, canceled)
        Handler.release.set()
        detail = self.wait_job(job["id"])
        self.assertEqual(detail["status"], "canceled", detail)
        self.assertEqual(len(self.calls_of("gate")), 1, "取消后不能再派发下一轮")
        run = self.run_row(job["ai_run_id"])
        self.assertEqual(run["status"], "canceled")

    # ---- 5. 额度耗尽如实停 -------------------------------------------------------

    def consume_one_run(self, limit: int) -> None:
        """先正常消耗一个名额；用独立 case，避免污染多轮用例的调用计数。"""
        self.settings("drain", limit=limit)
        status, job = self.enqueue()
        self.assertEqual(status, 202, job)
        self.assertEqual(self.run_job(job["id"])["status"], "success")

    def test_budget_exhausted_stops_with_honest_stop_reason(self) -> None:
        self.consume_one_run(2)
        self.settings("rounds", limit=2)
        status, job = self.enqueue(budget={"target_characters": 1200, "max_rounds": 6})
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        report = processed["result"]["budgeted_continuation"]
        # 今日上限 2，已有 1 次其它 run：只剩 1 个名额，第 2 轮就要如实停下。
        self.assertEqual(report["stop_reason"], "budget_exhausted")
        self.assertEqual(report["dispatched_calls"], 1)
        self.assertEqual(len(self.calls_of("rounds")), 1)
        self.assertTrue(processed["result"]["truncated"])
        self.assertLess(report["characters"], report["target_range"]["minimum"])
        # 不是「假装只花一次」：真实调用次数、真实字数都写进报告。
        self.assertEqual(report["rounds"], 1)
        self.assertEqual(report["characters"], 200)

    def test_budget_check_allows_exactly_remaining_rounds(self) -> None:
        self.consume_one_run(4)
        self.settings("rounds", limit=4)
        status, job = self.enqueue(budget={"target_characters": 1200, "max_rounds": 6})
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        report = processed["result"]["budgeted_continuation"]
        # 上限 4，已用 1：剩 3 个名额 -> 第 3 轮必须放行，第 4 轮才停（U + r <= 上限）。
        self.assertEqual(report["dispatched_calls"], 3)
        self.assertEqual(report["characters"], 600)
        self.assertEqual(report["stop_reason"], "budget_exhausted")

    # ---- 6. chunk 不撞号 + hash 可复现 ------------------------------------------

    def test_body_hash_is_reproducible_across_runs(self) -> None:
        self.settings("rounds")
        budget = {"target_characters": 600, "max_rounds": 6}
        status, first = self.enqueue(budget=budget)
        self.assertEqual(status, 202, first)
        first_result = self.run_job(first["id"])["result"]
        status, second = self.enqueue(budget=budget)
        self.assertEqual(status, 202, second)
        second_result = self.run_job(second["id"])["result"]
        self.assertEqual(first_result["text"], second_result["text"])
        first_run = self.run_row(first["ai_run_id"])
        second_run = self.run_row(second["ai_run_id"])
        self.assertEqual(first_run["output_hash"], second_run["output_hash"])
        self.assertEqual(first_run["output_hash"], server.hash_text(first_result["text"]))
        self.assert_chunks_form_one_segment(first["ai_run_id"], first_result["text"])
        self.assert_chunks_form_one_segment(second["ai_run_id"], second_result["text"])

    # ---- 离线分支与参数校验 ------------------------------------------------------

    def test_offline_budgeted_continuation_does_not_crash(self) -> None:
        status, job = self.enqueue(budget={"target_characters": 600, "max_rounds": 3})
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        result = processed["result"]
        report = result["budgeted_continuation"]
        self.assertEqual(result["mode"], "offline")
        self.assertEqual(result["usage_state"], "not_applicable")
        self.assertEqual(result["billing_state"], "not_dispatched")
        self.assertGreaterEqual(report["dispatched_calls"], 1)
        self.assertTrue(result["text"])
        self.assertEqual(self.calls_of("offline"), [])

    def test_budget_rejected_for_other_tasks_and_bad_fields(self) -> None:
        self.settings("rounds")
        status, rejected = self.request("POST", "/api/ai/queue", {
            "task": "polish", "budgeted_continuation": {"target_characters": 600}})
        self.assertEqual(status, 400, rejected)
        self.assertIn("只支持续写", rejected["error"])
        status, rejected = self.request("POST", "/api/ai/queue", {
            "task": "continue", "budgeted_continuation": {"target_characters": 600, "rounds": 2}})
        self.assertEqual(status, 400, rejected)
        status, rejected = self.request("POST", "/api/ai/queue", {
            "task": "continue", "budgeted_continuation": {"target_characters": 100}})
        self.assertEqual(status, 400, rejected)
        status, rejected = self.request("POST", "/api/ai/queue", {
            "task": "continue", "budgeted_continuation": {"target_characters": 600, "max_rounds": 3,
                                                          "hard_max_rounds": 2}})
        self.assertEqual(status, 400, rejected)
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_run").fetchone()[0], 0)
        self.assertEqual(self.calls_of("rounds"), [])


    # ---- 7. 首轮额度注入（冻结前） ------------------------------------------------

    def consent_of(self, run_id: str) -> dict:
        return json.loads(self.run_row(run_id)["consent_json"] or "{}")

    def test_first_round_prompt_carries_opening_budget(self) -> None:
        """首轮必须自带字数上限，否则引擎在首轮没有任何约束手段（sc-15 超冲）。"""
        self.settings("rounds")
        status, job = self.enqueue(budget={"target_characters": 600, "max_rounds": 3})
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        calls = self.calls_of("rounds")
        self.assertEqual(len(calls), 3)
        first = self.user_message(calls[0])
        # 普通键才进 material JSON：max_characters = 1.1 倍目标上限 - 已写 0 字。
        self.assertIn('"max_characters":660', first, "首轮提示必须带上本轮额度")
        self.assertIn('"remaining_characters":600', first)
        self.assertIn('"overshoot_hint":"本轮最多再写 660 字', first)
        # 第 1 轮不能提前带多轮材料（额度只走开场提示，不是「第 1 轮也跑引擎」）。
        self.assertNotIn('"continuation_round"', first)
        # 第 2 轮起用引擎的实时额度覆盖开场值，不能复读 660。
        second = self.user_message(calls[1])
        self.assertIn('"max_characters":460', second, "第 2 轮额度 = 660 - 已写 200")
        self.assertIn('"overshoot_hint":"本轮最多再写 460 字', second)
        self.assertNotIn('"max_characters":660', second, "开场额度不能继承到后续轮次")

    def test_first_round_messages_match_frozen_request_audit(self) -> None:
        """冻结即证据：第 1 轮真实发出的 messages 必须与 consent 里的审计逐字一致。"""
        self.settings("rounds")
        status, job = self.enqueue(budget={"target_characters": 600, "max_rounds": 3})
        self.assertEqual(status, 202, job)
        self.run_job(job["id"])
        calls = self.calls_of("rounds")
        audit = self.consent_of(job["ai_run_id"])["request_audit"]
        self.assertEqual(audit["payload"]["messages"], calls[0]["payload"]["messages"],
                         "首轮发出的 messages 必须就是被冻结的那份")
        frozen_material = self.user_message(calls[0])
        self.assertIn('"max_characters":660', frozen_material,
                      "额度必须在冻结之前注入，才能从 request_audit 审到")

    def test_continue_without_budget_has_no_opening_budget(self) -> None:
        """不带预算的路径一字不能变：首轮不能凭空多出额度字段。"""
        self.settings("single")
        status, job = self.enqueue()
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        first = self.user_message(self.calls_of("single")[0])
        for key in ("max_characters", "overshoot_hint", "remaining_characters"):
            self.assertNotIn(f'"{key}"', first, f"没要求多轮预算就不能注入 {key}")
        self.assertNotIn("_budgeted_continuation", first)

    # ---- 8. 停滞恢复期的配额必须送到模型 -----------------------------------------

    def engine_stall_context(self, budget: dict) -> dict[int, dict]:
        """用同一个引擎复算每轮 prompt_context，作为接线层的对照 oracle。"""
        texts = {2: STALL_TEXT}
        contexts: dict[int, dict] = {}

        def generate(prompt_context, round_index):
            contexts[round_index] = dict(prompt_context)
            return {"text": texts.get(round_index, round_text(round_index))}

        outcome = moyu_continuation.run_budgeted_continuation(
            generate,
            target_characters=budget["target_characters"],
            max_rounds=budget["max_rounds"],
            min_round_gain=budget["min_round_gain"],
            max_stall_retries=budget["max_stall_retries"],
            hard_max_rounds=budget["hard_max_rounds"],
        )
        self.assertTrue(outcome["target_reached"], "对照组应当写到目标")
        return contexts

    def test_stall_recovery_quota_reaches_the_prompt(self) -> None:
        """第 2 轮只写 30 字 → 引擎进入 v1.2 恢复期，配额必须原值送进后续轮提示词。"""
        self.settings("stall")
        status, job = self.enqueue(budget={"target_characters": 900, "max_rounds": 3})
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        contexts = self.engine_stall_context(job["budgeted_continuation"])
        calls = self.calls_of("stall")
        self.assertEqual(len(calls), len(contexts), "派发轮数必须与引擎一致")
        self.assertEqual(processed["result"]["budgeted_continuation"]["rounds"], len(contexts))

        round_three = contexts[3]
        minimum = round_three["min_characters"]
        retry_minimum = round_three["stall_retry_min_characters"]
        # 900 字目标、已写 230 字、距下限缺口 580 字、剩 10 轮：ceil(580/10) = 58 字。
        self.assertEqual(minimum, 58, "引擎给的恢复期配额是 58 字")
        self.assertEqual(retry_minimum, minimum)

        third = self.user_message(calls[2])
        self.assertIn(f'"min_characters":{minimum}', third, "整数配额必须进 material")
        self.assertIn(f'"stall_retry_min_characters":{retry_minimum}', third)
        self.assertIn(f"本轮至少写 {minimum} 字", third, "整数还要配一句人话")
        # 开场额度（target 900 / 上限 990）绝不能冒充恢复期配额。
        self.assertNotIn('"min_characters":900', third)
        self.assertNotIn('"min_characters":990', third)

        # 第 2 轮还没停滞，不能提前带配额。
        self.assertNotIn('"min_characters"', self.user_message(calls[1]))
        # 第 4 轮不是重试轮，但恢复期依旧带 pace 配额（锯齿才能收敛）。
        fourth = self.user_message(calls[3])
        self.assertIn(f'"min_characters":{contexts[4]["min_characters"]}', fourth)
        self.assertNotIn('"stall_retry_min_characters"', fourth)

        self.assert_chunks_form_one_segment(job["ai_run_id"], processed["result"]["text"])

    # ---- 9. 顶层余量必须跟着轮次走 ------------------------------------------------

    def prompt_material(self, call: dict) -> dict:
        """从 user 消息里解出 material JSON（模板是 <manuscript>…</manuscript>）。"""
        content = self.user_message(call)
        start = content.index("<manuscript>") + len("<manuscript>")
        end = content.index("</manuscript>", start)
        return json.loads(content[start:end].strip())

    def test_top_level_remaining_tracks_each_round(self) -> None:
        """顶层 remaining_characters 是首轮快照，必须每轮被引擎的新鲜值覆盖。"""
        self.settings("rounds")
        status, job = self.enqueue(budget={"target_characters": 600, "max_rounds": 3})
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        calls = self.calls_of("rounds")
        self.assertEqual(len(calls), 3)
        opening = self.prompt_material(calls[0])
        self.assertEqual(opening["remaining_characters"], 600, "第 1 轮就是开场快照")
        self.assertNotIn("continuation_round", opening, "第 1 轮不跑引擎材料")
        previous = opening["remaining_characters"]
        for call in calls[1:]:
            material = self.prompt_material(call)
            nested = material["continuation_round"]["remaining_characters"]
            self.assertEqual(material["remaining_characters"], nested,
                             "顶层与嵌套必须相等，否则模型看到两个互相矛盾的余量")
            self.assertLess(nested, previous, "每轮余量都要比上一轮更接近目标")
            self.assertIn(f"还差约 {nested} 字", material["continuation_requirement"])
            previous = nested
        # 首轮快照不能残留成第二个"余量"。
        self.assertNotIn('"remaining_characters":600',
                         self.user_message(calls[1]), "第 2 轮不能再出现开场快照")

    # ---- 10. round_target_ratio 透传（opt-in） ------------------------------------

    def test_round_target_ratio_absent_keeps_report_shape(self) -> None:
        """不传系数：报告字段一个不多，首轮额度仍是「上界 - 已写」。"""
        self.settings("rounds")
        status, job = self.enqueue(budget={"target_characters": 600, "max_rounds": 3})
        self.assertEqual(status, 202, job)
        self.assertNotIn("round_target_ratio", job["budgeted_continuation"],
                         "不传时不能凭空多出字段")
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        report = processed["result"]["budgeted_continuation"]
        self.assertNotIn("round_target_ratio", report, "默认路径的结果字段必须与改动前一致")
        self.assertNotIn("budget_plan", report, "默认路径不多出逐轮额度计划")
        self.assertEqual(self.prompt_material(self.calls_of("rounds")[0])["max_characters"], 660)
        consent = self.consent_of(job["ai_run_id"])
        self.assertNotIn("round_target_ratio", consent["budgeted_continuation"])

    def test_round_target_ratio_tightens_first_round_budget(self) -> None:
        """传 1.35：首轮额度按 ceil((目标-已写)/系数) 收紧，并全程可审计。"""
        self.settings("rounds")
        status, job = self.enqueue(budget={"target_characters": 600, "max_rounds": 3,
                                          "round_target_ratio": 1.35})
        self.assertEqual(status, 202, job)
        self.assertEqual(job["budgeted_continuation"]["round_target_ratio"], 1.35)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "success", processed)
        report = processed["result"]["budgeted_continuation"]
        self.assertEqual(report["round_target_ratio"], 1.35)
        self.assertEqual(report["characters"], 600)
        self.assertEqual(report["rounds"], 3)
        calls = self.calls_of("rounds")
        # 首轮：ceil((600-0)/1.35) = 445，没撞上 660 上界，来源是 ratio。
        opening = self.prompt_material(calls[0])
        self.assertEqual(opening["max_characters"], 445, "首轮额度必须按系数收紧")
        self.assertEqual(opening["remaining_characters"], 600)
        plan = report["budget_plan"]
        self.assertEqual(plan[0], {"round_index": 1, "characters_before": 0,
                                   "planned_max_characters": 445, "max_characters": 445,
                                   "source": "ratio", "delivered": False})
        # 第 2 轮：ceil((600-200)/1.35) = 297，且与引擎逐轮计划一致。
        second = self.prompt_material(calls[1])
        self.assertEqual(plan[1]["max_characters"], 297)
        self.assertEqual(second["max_characters"], plan[1]["max_characters"],
                         "提示词里的额度必须等于引擎逐轮计划")
        self.assertEqual(second["max_characters"], 297)
        # 冻结进 consent，可审计。
        self.assertEqual(self.consent_of(job["ai_run_id"])["budgeted_continuation"]
                         ["round_target_ratio"], 1.35)

    def test_round_target_ratio_is_validated(self) -> None:
        """越界或非数字一律 400，文案说明合法区间；且不留下任何 run。"""
        self.settings("rounds")
        for ratio in (0.9, 11, "1.35", True):
            with self.subTest(ratio=ratio):
                status, rejected = self.request("POST", "/api/ai/queue", {
                    "task": "continue",
                    "budgeted_continuation": {"target_characters": 600,
                                              "round_target_ratio": ratio}})
                self.assertEqual(status, 400, rejected)
                self.assertIn("1 到 10", rejected["error"])
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_run").fetchone()[0], 0)
        self.assertEqual(self.calls_of("rounds"), [])

    # ---- 8. 失败与取消：dispatched_calls 必须说真话（P12/P13） -------------------

    def assert_report_shape(self, report: dict, *, ratio: bool = False,
                            resume: bool = False) -> None:
        """报告键集合是契约：失败/取消也不许让调用方靠 if key in 试探形状。"""
        expected = {
            "task", "target_characters", "target_range", "characters", "target_reached",
            "within_tolerance", "stop_reason", "rounds", "dispatched_calls", "retries",
            "extended_rounds", "max_rounds", "hard_max_rounds", "counting",
            "candidate_only", "usage_state", "round_metrics",
            # t21 追加：种子字数与来源 run。非续写路径也必须出现，值写 0 / null。
            "seed_characters", "resumed_from_run",
        }
        if ratio:
            expected |= {"round_target_ratio", "budget_plan"}
        self.assertEqual(set(report), expected)
        if not resume:
            self.assertEqual(report["seed_characters"], 0, "没有种子就报 0，不许省略")
            self.assertIsNone(report["resumed_from_run"], "没有续写来源就写 null")

    def test_second_round_failure_counts_the_failed_call(self) -> None:
        """第 2 轮 500：调用已经发出去、可能已经计费，报告不能只报 1 次。"""
        self.settings("boom")
        budget = {"target_characters": 900, "max_rounds": 3, "round_target_ratio": 1.35}
        status, job = self.enqueue(budget=budget)
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        calls = self.calls_of("boom")
        self.assertEqual([call["request_id"] for call in calls],
                         [job["ai_run_id"], job["ai_run_id"] + "-r2"],
                         "第 2 轮必须真的派发过，否则这个用例证明不了 P12")
        self.assertEqual(processed["status"], "failed", processed)
        report = processed["result"]["budgeted_continuation"]
        # 修前这里报 1，而 provider 实收 2 —— 审计字段在唯一需要它的场景里说谎。
        self.assertEqual(report["dispatched_calls"], len(calls))
        self.assertEqual(report["dispatched_calls"], 2, "失败那一轮也是一次真实调用")
        self.assertEqual(report["rounds"], 1, "rounds 是已完成轮数，不许跟着改成 2")
        self.assertEqual(len(report["round_metrics"]), 1, "rounds 必须与 round_metrics 等长")
        self.assertEqual(report["stop_reason"], "failed")
        self.assertEqual(report["characters"], 200)
        self.assertIn(round_text(1), processed["result"]["text"], "第 1 轮正文不能丢")
        self.assert_report_shape(report, ratio=True)
        self.assertEqual(report["round_target_ratio"], 1.35)
        self.assertIsNone(report["budget_plan"], "没有引擎收尾计划就写 null，不许删键")
        self.assertIsNone(report["retries"])
        self.assertIsNone(report["extended_rounds"])
        self.assertEqual(report["usage_state"], "partial", "失败轮用量不可知，已完成轮只能当下界")
        self.assertFalse(report["target_reached"])
        self.assertFalse(report["within_tolerance"])
        self.assertEqual(report["max_rounds"], 3)
        self.assertEqual(report["hard_max_rounds"], 12)
        # 落库后的 consent 也能读到同一份报告，外部审计不必读 HTTP 响应。
        stored = self.run_row(job["ai_run_id"])
        self.assertEqual(json.loads(stored["consent_json"])["budgeted_continuation_report"]
                         ["dispatched_calls"], 2)

    def test_second_round_cancel_counts_the_canceled_call(self) -> None:
        """第 2 轮正在生成时取消：这一轮已经发出去，必须进账，第 1 轮正文要保住。"""
        self.settings("gate2")
        status, job = self.enqueue(budget={"target_characters": 900, "max_rounds": 3,
                                          "round_target_ratio": 1.35}, defer=False)
        self.assertEqual(status, 202, job)
        for _ in range(600):
            if len(self.calls_of("gate2")) >= 2:
                break
            time.sleep(0.01)
        self.assertEqual(len(self.calls_of("gate2")), 2,
                         "第 2 轮必须已经派发，这个用例才能证明失败轮进账")
        status, canceled = self.request("POST", f"/api/ai/jobs/{job['id']}/cancel", {})
        self.assertEqual(status, 202, canceled)
        Handler.release.set()
        detail = self.wait_job(job["id"])
        self.assertEqual(detail["status"], "canceled", detail)
        calls = self.calls_of("gate2")
        self.assertEqual(len(calls), 2, "取消后不能再派发第 3 轮")
        report = detail["result"]["budgeted_continuation"]
        self.assertEqual(report["dispatched_calls"], len(calls))
        self.assertEqual(report["dispatched_calls"], 2)
        self.assertEqual(report["stop_reason"], "canceled")
        self.assertEqual(report["rounds"], len(report["round_metrics"]),
                         "rounds 与 round_metrics 必须同长，不管取消落在哪一步")
        self.assertGreaterEqual(report["rounds"], 1)
        self.assertIn(round_text(1), detail["result"]["text"], "第 1 轮正文必须保住")
        self.assert_report_shape(report, ratio=True)
        self.assertEqual(report["round_target_ratio"], 1.35)
        self.assertIsNone(report["budget_plan"], "取消时没有收尾计划就写 null，不许删键")
        self.assertIsNone(report["retries"])
        self.assertIsNone(report["extended_rounds"])
        self.assertFalse(report["target_reached"])
        self.assertFalse(report["within_tolerance"])
        self.assertEqual(self.run_row(job["ai_run_id"])["status"], "canceled")

    # ---- 9. 断点续写：重试从已落盘的正文接着写 -----------------------------------

    def canceled_after_usage(self, *, case: str = "gate3",
                             budget: dict | None = None) -> tuple[dict, dict]:
        """造一个「第 2 轮已派发、用量已上报、随后取消」的可重试运行。

        只有这种中断（停得住、账又算得清）才允许续写；5xx 那种计费不明的失败
        仍然会被重试守卫挡住，见 test_retry_after_unknown_billing_is_still_refused。
        """
        self.settings(case)
        status, job = self.enqueue(budget=budget or {"target_characters": 600, "max_rounds": 3},
                                   defer=False)
        self.assertEqual(status, 202, job)
        for _ in range(600):
            if len(self.calls_of(case)) >= 2:
                break
            time.sleep(0.01)
        self.assertEqual(len(self.calls_of(case)), 2, "第 2 轮必须已派发")
        time.sleep(0.05)  # 让 provider 先把用量帧发出来，取消时账才是已知的
        status, canceled = self.request("POST", f"/api/ai/jobs/{job['id']}/cancel", {})
        self.assertEqual(status, 202, canceled)
        Handler.release.set()
        detail = self.wait_job(job["id"])
        self.assertEqual(detail["status"], "canceled", detail)
        return job, detail

    def test_retry_resumes_from_landed_body(self) -> None:
        """重试必须从已落盘的正文接着写：首轮提示带种子、字数不重复计、chunk 不撞号。"""
        job, canceled = self.canceled_after_usage()
        seed = "".join(row["text"] for row in self.chunk_rows(job["ai_run_id"]))
        self.assertTrue(seed.startswith(round_text(1)), "已落盘正文必须保住第 1 轮")
        self.assertEqual(self.consent_of(job["ai_run_id"])["billing_state"], "usage_reported",
                         "用量已上报的取消才可续写，计费不明的不能")
        before = len(self.calls_of("gate3"))
        status, retried = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
        self.assertEqual(status, 202, retried)  # 重试只会入队，不会同步跑完
        self.assertEqual(retried["retry_of"], job["ai_run_id"])
        processed = self.wait_job(retried["id"])  # 重试会自己排队执行，不能再手动 process
        self.assertEqual(processed["status"], "success", processed)

        calls = self.calls_of("gate3")[before:]
        self.assertTrue(calls, "重试必须真的派发新调用")
        first = self.user_message(calls[0])
        self.assertIn(seed, first, "种子正文必须出现在重试的首轮提示里")
        self.assertIn("还差约", first, "还要告诉模型差多少字")
        self.assertIn("从断点继续", first)

        report = processed["result"]["budgeted_continuation"]
        self.assertEqual(report["seed_characters"], moyu_output.count_characters(seed))
        self.assertEqual(report["resumed_from_run"], job["ai_run_id"])
        self.assertEqual(report["dispatched_calls"], len(calls), "种子不是调用，只算新派发")

        body = processed["result"]["text"]
        self.assertTrue(body.startswith(seed), "续写正文必须从种子接下去，不许改写开头")
        self.assertGreater(len(body), len(seed), "必须真的往下写了")
        self.assertEqual(report["characters"], moyu_output.count_characters(body))
        self.assertEqual(report["characters"],
                         report["seed_characters"]
                         + moyu_output.count_characters(body[len(seed):]),
                         "字符数把种子算进去，但不重复计")
        self.assert_chunks_form_one_segment(retried["ai_run_id"], body)

        consent = self.consent_of(retried["ai_run_id"])
        self.assertTrue(consent["resume_rebuilt_prompt"], "重建提示词要留痕，才不会被当成篡改")
        self.assertEqual(consent["resumed_from_run"], job["ai_run_id"])
        self.assertEqual(consent["seed_characters"], report["seed_characters"])
        self.assert_report_shape(report, resume=True)
        self.assertNotEqual(self.run_row(retried["ai_run_id"])["prompt_hash"],
                            self.run_row(job["ai_run_id"])["prompt_hash"],
                            "有种子就必须重拼提示词：prompt_hash 变了才说明没在重放冻结请求")

    def test_resume_report_treats_seed_as_round_zero(self) -> None:
        """种子是第 0 轮：轮次仍从 1 编号，累计字数从种子起算。"""
        job, _canceled = self.canceled_after_usage()
        status, retried = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
        self.assertEqual(status, 202, retried)  # 重试只会入队，不会同步跑完
        processed = self.wait_job(retried["id"])  # 重试会自己排队执行，不能再手动 process
        self.assertEqual(processed["status"], "success", processed)
        report = processed["result"]["budgeted_continuation"]
        seed = report["seed_characters"]
        self.assertGreater(seed, 0)
        # 目标 600、每轮 200 字：种子之外补到 600 就停。
        self.assertEqual(report["characters"], 600)
        self.assertEqual(report["rounds"], (600 - seed + 199) // 200)
        self.assertEqual(len(report["round_metrics"]), report["rounds"], "种子不算一轮")
        self.assertEqual(report["round_metrics"][0]["round_index"], 1, "轮次仍从 1 编号")
        self.assertEqual(report["round_metrics"][0]["cumulative_characters"], seed + 200,
                         "累计字数把种子算作第 0 轮")
        self.assertGreater(report["dispatched_calls"], 0)
        self.assertEqual(report["stop_reason"], "target_reached")
        self.assertTrue(report["within_tolerance"])

    def test_retry_without_budget_replays_the_frozen_request(self) -> None:
        """没有种子就退回今天的行为：逐字重放冻结请求，从第 1 轮重跑。"""
        self.settings("trunc")
        status, job = self.enqueue()
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "failed", processed)
        self.assertIsNone(processed["result"].get("budgeted_continuation"))
        first = self.calls_of("trunc")[0]
        status, retried = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
        self.assertEqual(status, 202, retried)  # 重试只会入队，不会同步跑完
        for key in ("budgeted_continuation", "resume_rebuilt_prompt", "resumed_from_run",
                    "seed_characters"):
            self.assertNotIn(key, retried, f"没有种子的重试不该出现 {key}")
        detail = self.wait_job(retried["id"])
        self.assertEqual(detail["status"], "success", detail)
        calls = self.calls_of("trunc")
        self.assertEqual(calls[1]["payload"], first["payload"],
                         "没有种子就必须逐字重放冻结请求，不许重建提示词")
        self.assertEqual(self.run_row(retried["ai_run_id"])["prompt_hash"],
                         self.run_row(job["ai_run_id"])["prompt_hash"],
                         "没有种子就必须逐字重放冻结请求：prompt_hash 一字不改")
        consent = self.consent_of(retried["ai_run_id"])
        for key in ("resume_rebuilt_prompt", "resumed_from_run", "seed_characters"):
            self.assertNotIn(key, consent)

    def test_retry_after_unknown_billing_is_still_refused(self) -> None:
        """计费不明的失败仍然只能人工核对：守卫是红线，续写不能绕过它。"""
        self.settings("boom")
        status, job = self.enqueue(budget={"target_characters": 600, "max_rounds": 3})
        self.assertEqual(status, 202, job)
        processed = self.run_job(job["id"])
        self.assertEqual(processed["status"], "failed", processed)
        self.assertEqual(self.consent_of(job["ai_run_id"])["billing_state"], "unknown")
        before = len(self.calls_of("boom"))
        status, refused = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {})
        self.assertEqual(status, 409, refused)
        self.assertIn("计费尚不明确", refused["error"])
        self.assertEqual(len(self.calls_of("boom")), before, "被拒绝的重试不许派发调用")
        with server.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_run").fetchone()[0], 1,
                             "被拒绝的重试不许建立新 run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
