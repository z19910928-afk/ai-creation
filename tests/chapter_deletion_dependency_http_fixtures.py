#!/usr/bin/env python3
"""Chapter deletion withdraws derived facts without deleting their history."""
from __future__ import annotations

import copy
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


PROJECT = "synthetic-chapter-deletion-book"
BRANCH = "synthetic-chapter-deletion-branch"
ACTOR = "synthetic-chapter-deletion-author"
CANARY = "DELETED_CHAPTER_FACT_CANARY"
KEEP_CANARY = "RETAINED_CHAPTER_FACT_CANARY"
AUTHOR_CANARY = "UNLINKED_AUTHOR_STATE_CANARY"
SOURCES = ("server.py", "moyu_context.py", "moyu_materials.py",
           "tests/chapter_deletion_dependency_http_fixtures.py")
REPORT = {"observations": [], "http": [], "attempted_provider_calls": 0,
          "attempted_worker_calls": 0}


def fingerprint():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in SOURCES}


def database_snapshot():
    with server.connect() as conn:
        result = {}
        for table in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            name = table[0]
            result[name] = [tuple(row) for row in conn.execute(
                f'SELECT * FROM "{name}"')]
        return result


class QuietHandler(server.Handler):
    def log_message(self, *_args):
        pass


class ChapterDeletionDependenciesHTTP(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.temp = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="moyu-chapter-deletion-")))
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
        with server.connect() as conn:
            initial = server.state_from_db(conn)
            self.chapter_id = initial["chapters"][0]["id"]
            self.entity_id = next(item["id"] for item in initial["records"]
                                  if item["type"] == "人物")
            self.quote = f"新人物持有{CANARY}。"
            server.update_chapter(conn, self.chapter_id, {
                "title": "即将删除的证据章",
                "body": self.quote + "\n这一章是唯一证据来源。",
            })
            server.create_chapter(conn, {
                "id": "retained-current-chapter", "title": "保留的当前章",
                "body": f"新人物推开窗户。{KEEP_CANARY}仍在桌上。",
            })
            self.keep_quote = f"{KEEP_CANARY}仍在桌上。"
            state = server.state_from_db(conn)
            state["memory"] = [
                self.memory("memory-delete-review", self.chapter_id,
                            self.quote, 0),
                self.memory("memory-retained-review", "retained-current-chapter",
                            self.keep_quote,
                            state["chapters"][1]["body"].index(self.keep_quote)),
            ]
            server.sync_state(conn, state)
            self.assertion_id = self.assertion_for(conn, "memory-delete-review")
            self.keep_assertion_id = self.assertion_for(conn, "memory-retained-review")
            for identifier, assertion, value in (
                    ("character-delete-review", self.assertion_id, CANARY),
                    ("character-retained-review", self.keep_assertion_id, KEEP_CANARY),
                    ("character-author-review", None, AUTHOR_CANARY)):
                conn.execute(
                    "INSERT INTO character_state(id,project_id,branch_id,entity_id,"
                    "attribute,value_json,event_id,valid_start,valid_end,"
                    "knowledge_scope_json,source_assertion_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, PROJECT, BRANCH, self.entity_id, identifier,
                     server.json_text({"item": value}), None, None, None, "{}",
                     assertion),
                )
            self.source_revision = conn.execute(
                "SELECT id,content_hash,canonical_text FROM document_revision "
                "WHERE document_node_id=? ORDER BY rowid DESC LIMIT 1",
                (self.chapter_id,),
            ).fetchone()
            self.source_revision = tuple(self.source_revision)
            self.source_state = server.state_from_db(conn)
            self.keep_memory = dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id='memory-retained-review'"
            ).fetchone())
            self.keep_assertion = dict(conn.execute(
                "SELECT * FROM assertion WHERE id=?", (self.keep_assertion_id,)
            ).fetchone())
            self.characters = [tuple(row) for row in conn.execute(
                "SELECT * FROM character_state ORDER BY id")]
            conn.commit()
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.http.server_port)

    def tearDown(self):
        try:
            self.http.shutdown()
            self.thread.join(5)
            self.http.server_close()
            self.assertFalse(self.thread.is_alive())
            REPORT["attempted_provider_calls"] += self.provider.call_count
            REPORT["attempted_worker_calls"] += self.worker.call_count
            self.provider.assert_not_called()
            self.worker.assert_not_called()
        finally:
            self.stack.close()

    def memory(self, identifier, chapter_id, quote, start):
        return {
            "id": identifier, "key": identifier, "type": "人物", "name": "新人物",
            "source": "章节原文", "note": quote, "status": "已确认",
            "source_chapter_id": chapter_id, "source_quote": quote,
            "source_start": start, "source_end": start + len(quote),
            "source_hash": server.hash_text(quote), "knowledge_scope": {},
            "known_by": [], "epistemic_mode": "world_truth",
            "valid_start": None, "valid_end": None,
        }

    @staticmethod
    def assertion_for(conn, memory_id):
        return conn.execute(
            "SELECT a.id FROM assertion a WHERE a.project_id=? AND a.branch_id=? "
            "AND EXISTS(SELECT 1 FROM json_each(a.evidence_json) e "
            "WHERE CASE WHEN e.type='object' THEN "
            "json_extract(e.value,'$.candidate_memory_id') END=?)",
            (PROJECT, BRANCH, memory_id),
        ).fetchone()[0]

    def call(self, method, path, payload=None):
        request = urllib.request.Request(
            self.base + path, method=method,
            data=server.json_text(payload).encode("utf-8")
            if payload is not None else None,
            headers={"Content-Type": "application/json",
                     "X-Moyu-Project": PROJECT, "X-Moyu-Branch": BRANCH},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status, result = response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status, result = exc.code, json.loads(exc.read())
        REPORT["http"].append({
            "test": self._testMethodName, "method": method, "path": path,
            "status": status, "error": result.get("error"),
        })
        return status, result

    def preview(self):
        status, result = self.call("POST", "/api/ai/prompt-preview", {
            "task": "continue", "chapter_id": "retained-current-chapter",
            "instruction": "继续找线索", "retrieval_profile": {
                "chapter_top_k": 10, "recent_window": 0,
                "memory_top_k": 10, "record_top_k": 0,
            },
        })
        if status != 200:
            return status, result, None
        material = json.loads(result["user"].split("<manuscript>\n", 1)[1]
                              .split("\n</manuscript>", 1)[0])
        return status, result, material

    def statuses(self):
        with server.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT cm.id,cm.status memory_status,ss.status span_status "
                "FROM candidate_memory cm JOIN source_span ss ON ss.id=cm.source_span_id "
                "WHERE cm.project_id=? AND cm.branch_id=? ORDER BY cm.id",
                (PROJECT, BRANCH),
            )]
            assertions = [dict(row) for row in conn.execute(
                "SELECT id,state FROM assertion WHERE project_id=? AND branch_id=? "
                "ORDER BY id", (PROJECT, BRANCH))]
            public = server.state_from_db(conn)
        return {
            "memory": rows, "assertions": assertions,
            "public_memory": [{key: item.get(key) for key in
                               ("id", "status", "stale", "source_span_status")}
                              for item in public["memory"]],
        }

    def delete(self, route):
        if route == "full_state":
            status, state = self.call("GET", "/api/state")
            self.assertEqual(status, 200, state)
            state["chapters"] = [item for item in state["chapters"]
                                 if item["id"] != self.chapter_id]
            return self.call("PUT", "/api/state", state)
        return self.call("DELETE", "/api/chapters/" + self.chapter_id, {
            "reason": "独立合成删除验证",
        })

    def assert_deleted_dependencies(self, route, before_verified=True):
        before = self.statuses()
        status, preview, material = self.preview()
        if before_verified:
            self.assertEqual(status, 200, preview)
            self.assertIn(CANARY, preview["user"])
        status, deleted = self.delete(route)
        after = self.statuses()
        preview_status, preview, material = self.preview()
        REPORT["observations"].append({
            "test": self._testMethodName, "route": route,
            "before": before, "delete_http_status": status,
            "delete_error": deleted.get("error"), "after": after,
            "preview_http_status": preview_status,
            "preview_error": preview.get("error"),
            "deleted_fact_in_prompt": CANARY in preview.get("user", ""),
        })
        self.assertEqual(status, 200, deleted)
        deleted_memory = next(item for item in after["memory"]
                              if item["id"] == "memory-delete-review")
        self.assertEqual(deleted_memory["memory_status"], "stale")
        self.assertEqual(deleted_memory["span_status"], "stale")
        self.assertEqual(next(item["state"] for item in after["assertions"]
                              if item["id"] == self.assertion_id), "candidate")
        self.assertEqual(preview_status, 200, preview)
        self.assertNotIn(CANARY, preview["user"])
        self.assertIn(KEEP_CANARY, preview["user"])
        self.assertIn(AUTHOR_CANARY, preview["user"])
        self.assertNotIn("character-delete-review", [
            item["id"] for item in material["story_structure"]["character_states"]])
        with server.connect() as conn:
            self.assertEqual(tuple(conn.execute(
                "SELECT id,content_hash,canonical_text FROM document_revision "
                "WHERE id=?", (self.source_revision[0],)).fetchone()),
                self.source_revision)
            self.assertEqual([tuple(row) for row in conn.execute(
                "SELECT * FROM character_state ORDER BY id")], self.characters)
            self.assertEqual(dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id='memory-retained-review'"
            ).fetchone()), self.keep_memory)
            self.assertEqual(dict(conn.execute(
                "SELECT * FROM assertion WHERE id=?", (self.keep_assertion_id,)
            ).fetchone()), self.keep_assertion)
            self.assertEqual(len(conn.execute(
                "SELECT * FROM deletion_event WHERE aggregate_id=?",
                (self.chapter_id,)).fetchall()), 1)
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])

    def test_full_state_omission_withdraws_facts_and_keeps_other_material(self):
        self.assert_deleted_dependencies("full_state")

    def test_delete_api_withdraws_facts_and_keeps_other_material(self):
        self.assert_deleted_dependencies("delete")

    def test_deleted_old_revision_and_already_stale_span_are_withdrawn(self):
        with server.connect() as conn:
            newer = server.uid("rev_")
            body = bytes(self.source_revision[2]).decode() + "\n后来的正文补充。"
            conn.execute(
                "INSERT INTO document_revision(id,document_node_id,parent_id,"
                "branch_id,content_hash,canonical_text,created_at,actor_id,operation) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (newer, self.chapter_id, self.source_revision[0], BRANCH,
                 server.hash_text(body), body.encode(), server.now(), ACTOR, "update"),
            )
            conn.execute("UPDATE branch SET head_revision=? WHERE id=?", (newer, BRANCH))
            conn.execute(
                "UPDATE source_span SET status='stale' WHERE id="
                "(SELECT source_span_id FROM candidate_memory "
                "WHERE id='memory-delete-review')")
        self.assert_deleted_dependencies("delete", before_verified=False)

    def test_unmodified_historical_accepted_memory_is_preserved(self):
        with server.connect() as conn:
            memory = conn.execute(
                "SELECT * FROM candidate_memory WHERE id='memory-delete-review'"
            ).fetchone()
            payload = json.loads(memory["payload_json"])
            payload["name"] = "历史显示名称"
            conn.execute("UPDATE candidate_memory SET payload_json=? WHERE id=?",
                         (server.json_text(payload), memory["id"]))
            old_revision = self.source_revision[0]
            newer = server.uid("rev_")
            body = bytes(self.source_revision[2]).decode() + "\n后来的正文补充。"
            conn.execute(
                "INSERT INTO document_revision(id,document_node_id,parent_id,"
                "branch_id,content_hash,canonical_text,created_at,actor_id,operation) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (newer, self.chapter_id, old_revision, BRANCH, server.hash_text(body),
                 body.encode(), server.now(), ACTOR, "update"),
            )
            conn.execute("UPDATE branch SET head_revision=? WHERE id=?", (newer, BRANCH))
            conn.commit()
            before_memory = dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id=?", (memory["id"],)
            ).fetchone())
            before_assertion = dict(conn.execute(
                "SELECT * FROM assertion WHERE id=?", (self.assertion_id,)
            ).fetchone())
        status, state = self.call("GET", "/api/state")
        self.assertEqual(status, 200, state)
        historical = next(item for item in state["memory"] if item["id"] == memory["id"])
        self.assertTrue(historical["stale"])
        status, result = self.call("PUT", "/api/state", state)
        self.assertEqual(status, 200, result)
        with server.connect() as conn:
            self.assertEqual(dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id=?", (memory["id"],)
            ).fetchone()), before_memory)
            self.assertEqual(dict(conn.execute(
                "SELECT * FROM assertion WHERE id=?", (self.assertion_id,)
            ).fetchone()), before_assertion)

    def test_mid_invalidation_failure_rolls_back_both_deletion_routes(self):
        with server.connect() as conn:
            conn.execute(
                "CREATE TRIGGER synthetic_fail_deleted_span "
                "BEFORE UPDATE OF status ON source_span "
                "WHEN NEW.status='stale' AND EXISTS(SELECT 1 FROM app_deleted_node d "
                "WHERE d.chapter_id=NEW.document_node_id) "
                "BEGIN SELECT RAISE(ABORT,'synthetic invalidation failure'); END")
        for route in ("full_state", "delete"):
            with self.subTest(route=route):
                before = database_snapshot()
                status, result = self.delete(route)
                REPORT["observations"].append({
                    "test": self._testMethodName, "route": route,
                    "status": status, "error": result.get("error"),
                    "database_unchanged": database_snapshot() == before,
                })
                self.assertEqual(status, 409, result)
                self.assertIn("synthetic invalidation failure", result["error"])
                self.assertEqual(database_snapshot(), before)


def main():
    output = os.environ.get("MOYU_CHAPTER_DELETION_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; choose a new report path")
    before, started = fingerprint(), datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        ChapterDeletionDependenciesHTTP)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    after = fingerprint()
    report = {
        "passed": result.wasSuccessful(), "methods": result.testsRun,
        "failures": [{"test": str(test), "traceback": trace}
                     for test, trace in result.failures],
        "errors": [{"test": str(test), "traceback": trace}
                   for test, trace in result.errors],
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after, "paid_provider_calls": 0,
        "scope": "temporary synthetic SQLite; real loopback HTTP; no provider",
        "claim_limit": "deletion dependency integrity; no literary-quality claim",
        **copy.deepcopy(REPORT),
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"http", "observations", "failures", "errors"}},
                     ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
