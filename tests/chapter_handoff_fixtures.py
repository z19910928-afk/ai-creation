"""Isolated chapter handoff preparation, source guards, and retry receipts."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_chapter_handoff as handoff
import moyu_workspace as workspace
import server


class ChapterHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="moyu-handoff-")
        cls.previous_db = server.DB_PATH
        server.DB_PATH = Path(cls.temporary.name) / "handoff.sqlite3"
        server.init_db()

    @classmethod
    def tearDownClass(cls):
        server.DB_PATH = cls.previous_db
        cls.temporary.cleanup()

    def setUp(self):
        self.conn = server.connect()
        created = server.new_project(self.conn, {"title": self._testMethodName})
        project = created["project"]
        self.project_id, self.branch_id = project["id"], project["branch_id"]
        actor = workspace.resolve_scope(self.conn, self.project_id, self.branch_id)[2]
        self.scope = workspace.use_scope(self.project_id, self.branch_id, actor)
        self.scope.__enter__()
        self.previous_id = created["state"]["chapters"][0]["id"]
        server.update_chapter(self.conn, self.previous_id, {
            "body": "林岚攥住罗盘，向尚未放行的渡口走去。😀",
            "chapter_goal": "拿到罗盘", "scene_plan": "旧屋取物", "pov": "林岚",
            "recap": "林岚已经拿到了罗盘。", "next_brief": "找到渡船，查明谁改动了航线。",
        })
        self.current_id = server.create_chapter(self.conn, {
            "title": "第二章 渡口", "body": "这一句原稿必须保留。",
            "hook": "守门人认出了罗盘。", "foreshadowing_op": "推进罗盘刻痕。",
        })
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.scope.__exit__(None, None, None)

    def preview(self):
        return handoff.preview(server, self.conn, self.current_id)

    def payload(self, preview=None):
        preview = preview or self.preview()
        return {"request_id": "handoff_request", "source_token": preview["source_token"],
                **preview["draft"]}

    def apply(self, payload):
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            result = handoff.apply(server, self.conn, self.current_id, payload)
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback()
            raise

    def current(self):
        return next(chapter for chapter in server.chapter_rows(self.conn)
                    if chapter["id"] == self.current_id)

    def test_preview_suggests_only_empty_goal_and_pov_without_writes(self):
        before = server.state_from_db(self.conn)
        events = self.conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
        preview = self.preview()
        self.assertEqual(preview["draft"], {
            "chapter_goal": "找到渡船，查明谁改动了航线。", "scene_plan": "", "pov": "林岚"})
        self.assertEqual(preview["suggested_fields"], ["chapter_goal", "pov"])
        self.assertEqual(preview["previous"]["id"], self.previous_id)
        self.assertEqual(server.state_from_db(self.conn), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0], events)

    def test_preview_preserves_existing_author_and_planned_fields(self):
        fields = {"chapter_goal": "作者已经另定目标", "scene_plan": "计划渡口谈判", "pov": "守门人"}
        server.update_chapter(self.conn, self.current_id, fields)
        self.conn.execute("INSERT INTO app_chapter_structure VALUES(?,?,?,?,?,?,?)",
                          (self.current_id, self.project_id, self.branch_id, "planned_volume",
                           "planned_chapter", "[]", server.now()))
        preview = self.preview()
        self.assertEqual(preview["draft"], fields)
        self.assertEqual(preview["suggested_fields"], [])

    def test_apply_only_changes_the_three_confirmed_task_fields(self):
        before = server.chapter_rows(self.conn)
        payload = {**self.payload(), "chapter_goal": "先核验渡船登记簿",
                   "scene_plan": "入场询问；遇阻；用罗盘交换线索", "pov": "守门人"}
        result = self.apply(payload)
        after = server.chapter_rows(self.conn)
        self.assertFalse(result["idempotent"])
        self.assertEqual(after[0], before[0])
        expected = {**before[1], **{key: payload[key] for key in handoff.FIELDS}}
        self.assertEqual(after[1], expected)
        self.assertEqual(len(after), 2)
        event = self.conn.execute(
            "SELECT payload_json FROM event_log WHERE aggregate_id=? "
            "AND event_type='chapter.handoff_prepared'", (self.current_id,),
        ).fetchone()
        self.assertEqual(json.loads(event["payload_json"])["previous_chapter_id"], self.previous_id)

    def test_retry_has_one_event_and_does_not_rewrite_later_edits(self):
        payload = self.payload()
        self.apply(payload)
        server.update_chapter(self.conn, self.current_id, {"chapter_goal": "作者随后修改的新目标"})
        self.conn.commit()
        before = server.state_from_db(self.conn)
        repeated = self.apply(payload)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(server.state_from_db(self.conn), before)
        self.assertEqual(self.current()["chapter_goal"], "作者随后修改的新目标")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE aggregate_id=? AND event_type='chapter.handoff_prepared'",
            (self.current_id,)).fetchone()[0], 1)

    def test_request_id_cannot_be_reused_for_different_content(self):
        payload = self.payload()
        self.apply(payload)
        with self.assertRaises(handoff.HandoffConflict):
            self.apply({**payload, "chapter_goal": "复用同一请求改成别的任务"})

    def test_previous_handoff_change_is_blocked_even_without_body_revision(self):
        payload = self.payload()
        server.update_chapter(self.conn, self.previous_id, {"next_brief": "作者更新过的上章交接"})
        self.conn.commit()
        before = server.state_from_db(self.conn)
        with self.assertRaises(handoff.HandoffConflict):
            self.apply(payload)
        self.assertEqual(server.state_from_db(self.conn), before)

    def test_previous_body_change_is_blocked(self):
        payload = self.payload()
        server.update_chapter(self.conn, self.previous_id, {"body": "上章正文已经修订。"})
        with self.assertRaises(handoff.HandoffConflict):
            self.apply(payload)

    def test_target_task_change_is_blocked_without_overwriting_author(self):
        payload = self.payload()
        server.update_chapter(self.conn, self.current_id, {"scene_plan": "作者新整理的场景"})
        with self.assertRaises(handoff.HandoffConflict):
            self.apply(payload)
        self.assertEqual(self.current()["scene_plan"], "作者新整理的场景")

    def test_target_body_change_is_blocked(self):
        payload = self.payload()
        server.update_chapter(self.conn, self.current_id, {"body": "作者刚刚补写的正文"})
        with self.assertRaises(handoff.HandoffConflict):
            self.apply(payload)
        self.assertEqual(self.current()["body"], "作者刚刚补写的正文")

    def test_different_predecessor_after_reordering_is_blocked(self):
        payload = self.payload()
        inserted = server.create_chapter(self.conn, {
            "title": "插入章", "next_brief": "来自插入章的交接"})
        self.conn.execute("UPDATE document_node SET ordinal=3 WHERE id=?", (self.current_id,))
        self.conn.execute("UPDATE document_node SET ordinal=2 WHERE id=?", (inserted,))
        with self.assertRaises(handoff.HandoffConflict):
            self.apply(payload)

    def test_cross_project_and_cross_branch_chapters_are_unavailable(self):
        payload = self.payload()
        other = server.new_project(self.conn, {"title": "其他作品"})["project"]
        scope = workspace.resolve_scope(self.conn, other["id"], other["branch_id"])
        with workspace.use_scope(*scope):
            with self.assertRaises(handoff.HandoffNotFound):
                handoff.preview(server, self.conn, self.current_id)
            with self.assertRaises(handoff.HandoffNotFound):
                handoff.apply(server, self.conn, self.current_id, payload)
        with workspace.use_scope(self.project_id, "another_branch", "another_actor"):
            with self.assertRaises(handoff.HandoffNotFound):
                handoff.apply(server, self.conn, self.current_id, payload)

    def test_deleted_chapter_and_first_chapter_cannot_be_prepared(self):
        with self.assertRaisesRegex(ValueError, "第一章"):
            handoff.preview(server, self.conn, self.previous_id)
        self.conn.execute("INSERT INTO app_deleted_node(chapter_id,deleted_at,reason) VALUES(?,?,?)",
                          (self.current_id, server.now(), "fixture deleted chapter"))
        with self.assertRaises(handoff.HandoffNotFound):
            self.preview()

    def test_empty_handoff_and_completed_target_are_blocked(self):
        server.update_chapter(self.conn, self.previous_id, {"next_brief": ""})
        with self.assertRaisesRegex(ValueError, "没有交接"):
            self.preview()
        server.update_chapter(self.conn, self.previous_id, {"next_brief": "新的交接"})
        server.update_chapter(self.conn, self.current_id, {"status": "完成"})
        with self.assertRaisesRegex(ValueError, "本章已经完成"):
            self.preview()

    def test_completed_book_is_blocked(self):
        self.conn.execute("UPDATE app_project_meta SET book_status='complete' WHERE project_id=?",
                          (self.project_id,))
        with self.assertRaisesRegex(ValueError, "全书已完稿"):
            self.preview()

    def test_archived_project_cannot_apply_a_previously_opened_preview(self):
        payload = self.payload()
        self.conn.execute("UPDATE app_workspace SET archived=1 WHERE project_id=?",
                          (self.project_id,))
        self.conn.commit()
        before = server.state_from_db(self.conn)
        with self.assertRaisesRegex(ValueError, "作品已归档"):
            self.apply(payload)
        self.assertEqual(server.state_from_db(self.conn), before)

    def test_nonactive_branch_cannot_apply_a_previously_opened_preview(self):
        payload = self.payload()
        for status in ("archived", "merged"):
            with self.subTest(status=status):
                self.conn.execute("UPDATE branch SET status=? WHERE id=?", (status, self.branch_id))
                self.conn.commit()
                before = self.current()
                with self.assertRaisesRegex(ValueError, "版本已封存"):
                    self.apply(payload)
                self.assertEqual(self.current(), before)

    def test_long_handoff_is_kept_in_source_without_silent_truncation(self):
        original = "交接😀" * 2000
        server.update_chapter(self.conn, self.previous_id, {"next_brief": original})
        preview = self.preview()
        self.assertEqual(preview["previous"]["next_brief"], original)
        self.assertEqual(preview["draft"]["chapter_goal"], "")
        self.assertTrue(preview["warnings"])
        self.assertNotIn("chapter_goal", preview["suggested_fields"])

    def test_validation_rejects_unknown_fields_types_and_invalid_identifiers(self):
        payload = self.payload()
        invalid = [
            {**payload, "body": "不得改正文"}, {**payload, "scene_plan": 123},
            {**payload, "pov": None}, {**payload, "chapter_goal": " \n"},
            {**payload, "chapter_goal": "字" * 5001}, {**payload, "pov": "人" * 501},
            {**payload, "scene_plan": "景" * 20001}, {**payload, "source_token": "bad"},
            {**payload, "request_id": ""}, {**payload, "request_id": "../request"},
        ]
        for item in invalid:
            with self.subTest(item=list(item)):
                with self.assertRaises(ValueError):
                    self.apply(copy.deepcopy(item))

    def test_http_concurrent_retry_writes_once_and_stale_metadata_is_rejected(self):
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        route = f"http://127.0.0.1:{http.server_port}/api/chapters/{self.current_id}/handoff"

        def request(method, payload=None):
            raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
            req = urllib.request.Request(
                route, data=raw, method=method,
                headers={"Content-Type": "application/json", "X-Moyu-Project": self.project_id,
                         "X-Moyu-Branch": self.branch_id})
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        try:
            code, preview = request("GET")
            self.assertEqual(code, 200, preview)
            payload = self.payload(preview)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: request("POST", payload), range(2)))
            self.assertTrue(all(code == 200 for code, _ in results), results)
            self.assertEqual(sorted(result["idempotent"] for _, result in results), [False, True])
            events = self.conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE aggregate_id=? "
                "AND event_type='chapter.handoff_prepared'", (self.current_id,)).fetchone()[0]
            self.assertEqual(events, 1)
            code, preview = request("GET")
            self.assertEqual(code, 200, preview)
            stale = {**self.payload(preview), "request_id": "second_http_request"}
            server.update_chapter(self.conn, self.previous_id, {"next_brief": "交接在另一个窗口更新"})
            self.conn.commit()
            before = server.state_from_db(self.conn)
            code, error = request("POST", stale)
            self.assertEqual(code, 409, error)
            self.assertIn("已经变化", error["error"])
            self.assertEqual(server.state_from_db(self.conn), before)
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
