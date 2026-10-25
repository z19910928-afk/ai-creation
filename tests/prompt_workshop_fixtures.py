#!/usr/bin/env python3
"""提示词工坊：模板 CRUD、内置保护、重置与默认提示词逐字节快照。

Run: python tests/prompt_workshop_fixtures.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import (  # noqa: E402
    moyu_agent_chat, moyu_output, moyu_planning, moyu_prompt_templates,
    moyu_revision, moyu_settings, server,
)

PROJECT = "synthetic-prompt-book"
BRANCH = "synthetic-prompt-branch"
ACTOR = "synthetic-prompt-author"
SNAPSHOT_PATH = ROOT / "tests" / "prompt_workshop_snapshot.json"

# 与改造前快照采集脚本完全相同的输入，保证对比的是同一批字节。
CONFIG = {"base_url": "http://127.0.0.1:9/v1", "model": "offline-synthetic"}
CHAPTER = {"id": "ch_1", "title": "第一章 雨夜", "body": "他推开门，风灌了进来。"}
TASK_FORMATS = {
    "continue": "text", "polish": "text", "outline": "json", "scene": "json",
    "summary": "json", "check": "json", "brainstorm": "json",
    "chapter_analysis": "json", "semantic_review": "json",
}


class QuietHandler(server.Handler):
    def log_message(self, *_args):
        pass


class PromptWorkshopHTTP(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.temp = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="moyu-prompt-workshop-")))
        for field, value in (("DB_PATH", self.temp / "book.sqlite3"),
                             ("PROJECT_ID", PROJECT), ("BRANCH_ID", BRANCH),
                             ("ACTOR_ID", ACTOR)):
            self.stack.enter_context(patch.object(server, field, value))
        self.stack.enter_context(patch.dict(os.environ, {
            "MOYU_AI_API_KEY": "", "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "",
            "MOYU_SECRET_KEY": "",
            "MOYU_CREDENTIALS_DIR": str(self.temp / "synthetic-credentials"),
        }))
        self.stack.enter_context(patch.object(server, "ai_settings", return_value={
            "base_url": "http://127.0.0.1:9/v1", "model": "offline-synthetic",
            "api_key": "", "max_tokens": 1200, "styles": {},
        }))
        self.provider = self.stack.enter_context(patch.object(
            server, "call_ai_provider", side_effect=AssertionError("provider forbidden")))
        self.worker = self.stack.enter_context(patch.object(
            server, "start_ai_worker", side_effect=AssertionError("worker forbidden")))
        server.init_db()
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.thread.join(5)
        self.http.server_close()
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.provider.call_count, 0)
        self.assertEqual(self.worker.call_count, 0)
        self.stack.close()

    # -- helpers ---------------------------------------------------------- #
    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=body, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get(self, path: str) -> tuple[int, dict]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict | None = None) -> tuple[int, dict]:
        return self.request("POST", path, payload if payload is not None else {})

    def put(self, path: str, payload: dict) -> tuple[int, dict]:
        return self.request("PUT", path, payload)

    def delete(self, path: str) -> tuple[int, dict]:
        return self.request("DELETE", path, {})

    def templates(self) -> list[dict]:
        status, body = self.get("/api/prompt-templates")
        self.assertEqual(status, 200)
        return body["templates"]

    def builtin(self, action: str) -> dict:
        identifier = moyu_prompt_templates.builtin_id(action)
        for item in self.templates():
            if item["id"] == identifier:
                return item
        self.fail(f"缺少内置模板：{action}")

    # -- 列表 / 详情 ------------------------------------------------------- #
    def test_list_returns_all_builtin_templates(self):
        items = self.templates()
        self.assertEqual(len(items), len(moyu_prompt_templates.BUILTIN_TEMPLATES))
        self.assertTrue(all(item["is_builtin"] for item in items))
        self.assertEqual({item["action"] for item in items},
                         set(moyu_prompt_templates.BUILTIN_TEMPLATES))
        self.assertEqual(len({item["id"] for item in items}), len(items), "id 必须唯一")
        for item in items:
            self.assertEqual(item["body"],
                             moyu_prompt_templates.builtin_body(item["action"]))
            self.assertEqual(item["version"], 1)
            self.assertEqual(item["id"], moyu_prompt_templates.builtin_id(item["action"]))

    def test_get_template_detail_and_missing(self):
        status, body = self.get(
            "/api/prompt-templates/" + moyu_prompt_templates.builtin_id("ai.task.continue"))
        self.assertEqual(status, 200)
        self.assertEqual(body["action"], "ai.task.continue")
        self.assertTrue(body["is_builtin"])
        status, body = self.get("/api/prompt-templates/pt_missing")
        self.assertEqual(status, 404)
        self.assertIn("不存在", body["error"])

    # -- 自定义模板 CRUD --------------------------------------------------- #
    def test_create_update_delete_custom_template(self):
        status, created = self.post("/api/prompt-templates", {
            "action": "ai.task.continue", "name": "我的续写提示",
            "body": "写得更克制：{{instruction}}",
        })
        self.assertEqual(status, 201)
        self.assertFalse(created["is_builtin"])
        self.assertEqual(created["version"], 1)
        self.assertTrue(created["id"].startswith("pt_"))
        identifier = created["id"]

        status, updated = self.put("/api/prompt-templates/" + identifier,
                                   {"name": "更克制的续写", "body": "只写动作：{{instruction}}"})
        self.assertEqual(status, 200)
        self.assertEqual(updated["name"], "更克制的续写")
        self.assertEqual(updated["body"], "只写动作：{{instruction}}")
        self.assertEqual(updated["version"], 2)

        status, listed = self.get("/api/prompt-templates")
        self.assertEqual(status, 200)
        self.assertIn(identifier, [item["id"] for item in listed["templates"]])

        status, deleted = self.delete("/api/prompt-templates/" + identifier)
        self.assertEqual(status, 200)
        self.assertEqual(deleted["deleted"], identifier)
        status, _ = self.get("/api/prompt-templates/" + identifier)
        self.assertEqual(status, 404)

    def test_custom_override_changes_render_until_deleted(self):
        default = moyu_prompt_templates.render("ai.task.continue")
        status, created = self.post("/api/prompt-templates", {
            "action": "ai.task.continue", "name": "覆盖", "body": "自定义正文",
        })
        self.assertEqual(status, 201)
        self.assertEqual(moyu_prompt_templates.render("ai.task.continue"), "自定义正文")
        self.assertEqual(self.builtin("ai.task.continue")["body"], default,
                         "覆盖不能改动内置模板本身")
        self.delete("/api/prompt-templates/" + created["id"])
        self.assertEqual(moyu_prompt_templates.render("ai.task.continue"), default)

    # -- 内置保护 ---------------------------------------------------------- #
    def test_builtin_cannot_be_updated_or_deleted(self):
        identifier = moyu_prompt_templates.builtin_id("ai.task.polish")
        before = self.builtin("ai.task.polish")

        status, body = self.put("/api/prompt-templates/" + identifier, {"body": "篡改"})
        self.assertEqual(status, 409)
        self.assertIn("内置模板不可修改", body["error"])

        status, body = self.put("/api/prompt-templates/" + identifier, {"name": "改名"})
        self.assertEqual(status, 409, "只改名称也必须拒绝，内置模板整体受保护")

        status, body = self.delete("/api/prompt-templates/" + identifier)
        self.assertEqual(status, 409)
        self.assertIn("内置模板不可删除", body["error"])

        after = self.builtin("ai.task.polish")
        self.assertEqual(after["body"], before["body"], "被拒绝后正文不得变化")
        self.assertEqual(after["name"], before["name"])
        self.assertEqual(after["version"], before["version"])

    # -- 重置 -------------------------------------------------------------- #
    def test_reset_builtin_restores_factory_body(self):
        identifier = moyu_prompt_templates.builtin_id("ai.task.summary")
        factory = moyu_prompt_templates.builtin_body("ai.task.summary")
        with server.connect() as conn:
            conn.execute(
                "UPDATE app_prompt_template SET body='历史残留文案',version=7 WHERE id=?",
                (identifier,))
            conn.commit()
            moyu_prompt_templates._refresh(conn)
        self.assertEqual(self.builtin("ai.task.summary")["body"], "历史残留文案")
        self.assertEqual(moyu_prompt_templates.render("ai.task.summary"), "历史残留文案")

        status, body = self.post(f"/api/prompt-templates/{identifier}/reset")
        self.assertEqual(status, 200)
        self.assertEqual(body["id"], identifier)
        self.assertEqual(body["body"], factory, "重置必须恢复出厂正文")
        self.assertEqual(body["version"], 8, "正文变化时版本号递增")
        self.assertEqual(moyu_prompt_templates.render("ai.task.summary"), factory)
        self.assertEqual(self.builtin("ai.task.summary")["body"], factory)

    def test_reset_custom_falls_back_to_builtin_body(self):
        factory = moyu_prompt_templates.builtin_body("chat.user")
        status, created = self.post("/api/prompt-templates", {
            "action": "chat.user", "name": "我的包装", "body": "作者说：{{content}}",
        })
        self.assertEqual(status, 201)
        status, body = self.post(f"/api/prompt-templates/{created['id']}/reset")
        self.assertEqual(status, 200)
        self.assertEqual(body["body"], factory)
        self.assertEqual(body["version"], 2)
        self.assertFalse(body["is_builtin"])
        rendered = moyu_prompt_templates.render("chat.user", material="{}", content="继续")
        self.assertEqual(rendered, factory.replace("{{material}}", "{}").replace(
            "{{content}}", "继续"))

    def test_reset_is_idempotent_for_factory_body(self):
        identifier = moyu_prompt_templates.builtin_id("probe.user")
        status, first = self.post(f"/api/prompt-templates/{identifier}/reset")
        self.assertEqual(status, 200)
        self.assertEqual(first["version"], 1, "出厂正文未变时不应递增版本")
        status, second = self.post(f"/api/prompt-templates/{identifier}/reset")
        self.assertEqual(status, 200)
        self.assertEqual(second["version"], 1)

    # -- 参数校验 ---------------------------------------------------------- #
    def test_invalid_payloads_are_rejected(self):
        status, body = self.post("/api/prompt-templates", {"name": "缺动作", "body": "x"})
        self.assertEqual(status, 400)
        self.assertIn("提示词动作", body["error"])
        status, body = self.post("/api/prompt-templates",
                                 {"action": "ai.task.continue", "name": "缺正文"})
        self.assertEqual(status, 400)
        self.assertIn("正文", body["error"])
        status, body = self.post("/api/prompt-templates",
                                 {"action": "ai.task.continue", "name": "", "body": "x"})
        self.assertEqual(status, 400)
        self.assertIn("名称", body["error"])
        status, body = self.put("/api/prompt-templates/pt_missing", {"body": "x"})
        self.assertEqual(status, 404)
        status, body = self.delete("/api/prompt-templates/pt_missing")
        self.assertEqual(status, 404)
        status, body = self.post("/api/prompt-templates/pt_missing/reset")
        self.assertEqual(status, 404)

    def test_render_rejects_unknown_action_and_missing_variables(self):
        with self.assertRaises(ValueError) as caught:
            moyu_prompt_templates.render("nope.nope")
        self.assertIn("未知的提示词动作", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            moyu_prompt_templates.render("ai.style_note", tone="冷峻")
        self.assertIn("缺少变量", str(caught.exception))
        # 花括号是普通字符，不能被当成格式化字段。
        self.assertEqual(moyu_prompt_templates._render_body("a {b} c", {}), "a {b} c")

    # -- seed -------------------------------------------------------------- #
    def test_seed_is_idempotent_and_repairs_drift(self):
        with server.connect() as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM app_prompt_template").fetchone()[0]
            versions = {row["id"]: row["version"] for row in conn.execute(
                "SELECT id,version FROM app_prompt_template")}
            moyu_prompt_templates.seed_builtin_templates(conn)
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM app_prompt_template").fetchone()[0]
            self.assertEqual(before, after)
            for row in conn.execute("SELECT id,version FROM app_prompt_template"):
                self.assertEqual(versions[row["id"]], row["version"], "正常启动不应改版本")

    # -- 逐字节快照 -------------------------------------------------------- #
    def test_default_prompts_are_byte_identical(self):
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["captured_from"],
                         "pre-refactor backend prompt assembly (golden bytes)")

        # 1) 10 个任务指令。
        self.assertEqual(server.AI_TASKS, snapshot["task_instructions"])

        # 2) ai_prompt_preview：9 个最小用例 + 1 个条件用例。
        cases = {}
        for task, response_format in TASK_FORMATS.items():
            preview = server.ai_prompt_preview(
                task, {"current_chapter": dict(CHAPTER)}, "保持原有情节推进。",
                response_format, config=dict(CONFIG))
            cases[f"minimal.{task}"] = {
                "task": task, "response_format": response_format,
                "system": preview["system"], "user": preview["user"],
            }
        conditional_context = {
            "current_chapter": dict(CHAPTER),
            "reference_passages": [{"id": "ref_1", "title": "参考书", "text": "风从海上吹来。"}],
        }
        preview = server.ai_prompt_preview(
            "continue", conditional_context, "续写600字，正文控制在500至800字。", "text",
            config={**CONFIG, "styles": {"tone": "冷峻克制", "instructions": "多用短句。"}})
        cases["conditional.reference_style.continue"] = {
            "task": "continue", "response_format": "text",
            "system": preview["system"], "user": preview["user"],
        }
        self.assertEqual(set(cases), set(snapshot["ai_prompt_preview"]))
        for name, expected in snapshot["ai_prompt_preview"].items():
            self.assertEqual(cases[name]["system"], expected["system"], f"{name} system 不一致")
            self.assertEqual(cases[name]["user"], expected["user"], f"{name} user 不一致")

        # 3) 写作总管消息。
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE app_agent_chat_turn(
                id TEXT, content TEXT, reply TEXT, references_json TEXT, job_id TEXT,
                session_id TEXT, project_id TEXT, branch_id TEXT, status TEXT);
            CREATE TABLE app_ai_job(id TEXT, project_id TEXT, branch_id TEXT, result_json TEXT);
        """)
        context = {"project": {"name": "测试作品"}, "current_chapter": dict(CHAPTER)}
        messages = moyu_agent_chat._messages(
            self._fake_api(), conn, "s_1", context, "帮我看看这一章。")
        self.assertEqual([item["role"] for item in messages],
                         snapshot["agent_chat_messages"]["roles"])
        self.assertEqual(messages[0]["content"], snapshot["agent_chat_messages"]["system"])
        self.assertEqual(messages[-1]["content"], snapshot["agent_chat_messages"]["user"])

        # 4) 分阶段规划指令。
        blueprint = {"premise": "一个关于雨夜的故事。", "genre": "悬疑", "audience": "成年读者",
                     "tone": "冷峻", "contract": "每章 3000 字。", "volumes": []}
        row = {"project_id": "p", "branch_id": "b", "id": "pw_1"}
        for stage, expected in snapshot["planning_instructions"].items():
            payload = moyu_planning._source_payload(row, stage, blueprint, 3, "hash", 2000)
            self.assertEqual(payload["instruction"], expected, f"{stage} 规划指令不一致")

        # 5) 候选整理请求。
        contract = moyu_output.output_contract("continue", "写 600 字。")
        payload = {"model": "m", "messages": [
            {"role": "system", "content": "原始系统要求。"},
            {"role": "user", "content": "原始用户消息。"},
        ]}
        revised = moyu_revision.build_revision_request(
            payload, "continue", "候选正文第一句。", contract, "更紧凑。")
        self.assertEqual(revised["messages"][0]["content"],
                         snapshot["revision_request"]["system"])
        self.assertEqual(revised["messages"][-1]["content"],
                         snapshot["revision_request"]["user"])

        # 6) 连接测试消息。
        probe = moyu_settings.probe_messages()
        self.assertEqual(probe[0]["content"], snapshot["probe_messages"]["system"])
        self.assertEqual(probe[1]["content"], snapshot["probe_messages"]["user"])

        # 7) 候选输出要求（保留代码生成，但快照证明改造未影响输出）。
        constraints_contract = moyu_output.output_contract(
            "continue", "续写600字，正文控制在500至800字。")
        self.assertEqual(moyu_output.prompt_constraints("continue", constraints_contract),
                         snapshot["output_constraints"]["continue"])
        self.assertEqual(
            moyu_output.prompt_constraints(
                "polish", moyu_output.output_contract("polish", "润色，不要超过900字。")),
            snapshot["output_constraints"]["polish"])

    @staticmethod
    def _fake_api():
        class _Api:
            project_id = staticmethod(lambda: "p")
            branch_id = staticmethod(lambda: "b")
            json_text = staticmethod(
                lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        return _Api


if __name__ == "__main__":
    unittest.main(verbosity=2)
