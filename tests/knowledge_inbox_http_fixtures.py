"""Read-only HTTP contracts for card links and the whole-book writing inbox."""
from __future__ import annotations

from contextlib import closing
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


class QuietHandler(server.Handler):
    def log_message(self, *_args):
        pass


class KnowledgeInboxHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="moyu-knowledge-inbox-http-")
        cls.old_path = server.DB_PATH
        server.DB_PATH = Path(cls.temporary.name) / "synthetic.sqlite3"
        cls.environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "http://127.0.0.1:1/v1", "MOYU_AI_MODEL": "",
            "MOYU_AI_API_KEY": "", "MOYU_SECRET_KEY": "",
            "MOYU_CREDENTIALS_DIR": str(Path(cls.temporary.name) / "credentials"),
        })
        cls.environment.start()
        cls.blockers = [patch.object(server, name, side_effect=AssertionError("No model dispatch"))
                        for name in ("call_ai_provider", "start_ai_worker")]
        cls.mocks = [blocker.start() for blocker in cls.blockers]
        server.init_db()
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"
        state = cls.call("GET", "/api/state")[1]
        cls.chapter = state["chapters"][0]["id"]
        cls.quote = "林岚把🗝️铜钥匙放回门边。"
        state["chapters"][0].update(body=cls.quote + "\n雨还没有停。", pov="林岚")
        state["records"] = [
            {"id": "http_lan", "name": "林岚", "type": "人物", "description": "合成人物。"},
            {"id": "http_key", "name": "铜钥匙", "type": "物品", "description": "合成物品。"},
        ]
        assert cls.call("PUT", "/api/state", state)[0] == 200
        status, relation = cls.call("POST", "/api/story/relationships", {
            "from_entity_id": "http_lan", "to_entity_id": "http_key",
            "relation_type": "持有", "note": "作者暂定关系。",
        })
        assert status == 201, relation
        cls.relation = relation["id"]
        status, cls.memory = cls.call("POST", "/api/memory", {
            "type": "人物", "name": "林岚", "status": "accepted",
            "source_chapter_id": cls.chapter, "source_quote": cls.quote,
            "source_start": 0, "source_end": len(cls.quote),
        })
        assert status == 201, cls.memory
        status, cls.candidate = cls.call("POST", "/api/memory", {
            "type": "人物", "name": "林岚", "status": "candidate",
            "source_chapter_id": cls.chapter, "source_quote": cls.quote,
            "source_start": 0, "source_end": len(cls.quote),
        })
        assert status == 201, cls.candidate
        cls.agents = []
        for index in range(56):
            status, value = cls.call("POST", "/api/writing-agent", {
                "request_id": f"http_inbox_request_{index}", "chapter_id": cls.chapter,
                "query": f"合成待办计划 {index}", "mode": "inspect",
            })
            assert status == 200, value
            cls.agents.append(value["session"]["id"])
        status, other = cls.call("POST", "/api/projects", {"title": "隔离的合成作品"})
        assert status == 201, other
        cls.other = other["project"]["id"]

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
        server.DB_PATH = cls.old_path
        cls.temporary.cleanup()

    @classmethod
    def call(cls, method, route, payload=None, project="default-project", branch=None):
        headers = {"Content-Type": "application/json", "X-Moyu-Project": project}
        if branch is not None:
            headers["X-Moyu-Branch"] = branch
        request = urllib.request.Request(cls.base + route, method=method, headers=headers,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode())
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def setUp(self):
        with closing(server.connect()) as conn:
            self.before = tuple(conn.iterdump())

    def tearDown(self):
        with closing(server.connect()) as conn:
            self.assertEqual(tuple(conn.iterdump()), self.before, "GET must not change the database")

    def test_card_links_and_sources_retain_stable_identity(self):
        status, data = self.call("GET", f"/api/records/http_lan/knowledge?chapter_id={self.chapter}")
        self.assertEqual(status, 200)
        self.assertEqual(data["record"]["id"], "http_lan")
        self.assertEqual(data["outgoing"][0]["id"], self.relation)
        self.assertEqual(data["outgoing"][0]["other_record"]["id"], "http_key")
        self.assertEqual(data["outgoing"][0]["epistemic_mode"], "author_plan")
        self.assertEqual(data["summary"]["sources"], 3)
        self.assertTrue(all(row["evidence"]["status"] == "valid" for row in data["sources"]))
        self.assertTrue(all(row["evidence"]["source_quote"] == self.quote for row in data["sources"]))
        incoming = self.call("GET", f"/api/records/http_key/knowledge?chapter_id={self.chapter}")[1]
        self.assertEqual(incoming["incoming"][0]["id"], self.relation)

    def test_unconfirmed_candidate_has_its_own_source_before_any_assertion(self):
        identifier = self.candidate["id"]
        for suffix in ("", "/evidence"):
            with self.subTest(suffix=suffix):
                status, data = self.call("GET", f"/api/memory/{identifier}" + suffix)
                self.assertEqual(status, 200)
                self.assertEqual(data["assertions"], [])
                self.assertEqual(len(data["evidence"]), 1)
                direct = data["evidence"][0]
                self.assertEqual(direct["candidate_memory_id"], identifier)
                self.assertEqual(direct["source_span"]["quote"], self.quote)
                self.assertEqual(direct["source_span"]["start_codepoint"], 0)
                self.assertTrue(direct["source_span"]["current"])
                self.assertFalse(direct["stale"])
                self.assertEqual(direct["candidate_memory"]["status"], "candidate")
                self.assertEqual(self.call("GET", f"/api/memory/{identifier}" + suffix,
                                           project=self.other)[0], 404)

    def test_unselected_card_is_readable_but_not_current_material(self):
        status, data = self.call("GET", "/api/records/http_lan/knowledge")
        self.assertEqual(status, 200)
        self.assertEqual(data["availability"]["status"], "unselected")
        self.assertFalse(data["availability"]["allowed"])

    def test_card_rejects_bad_pagination_and_duplicate_fields(self):
        for query in ("limit=0", "limit=101", "limit=no", "offset=-1", "offset=1.2",
                      "chapter_id=", "limit=5&limit=10", "chapter_id=x&chapter_id=y"):
            with self.subTest(query=query):
                self.assertEqual(self.call("GET", "/api/records/http_lan/knowledge?" + query)[0], 400)

    def test_card_scope_missing_records_and_malformed_paths(self):
        for route, project in (
            ("/api/records/http_lan/knowledge", self.other),
            ("/api/records/missing_card/knowledge", "default-project"),
            ("/api/records/http_lan/knowledge?chapter_id=missing_chapter", "default-project"),
        ):
            with self.subTest(route=route, project=project):
                status, data = self.call("GET", route, project=project)
                self.assertEqual(status, 404)
                self.assertNotIn("record", data)
        self.assertEqual(self.call("GET", "/api/records/http_lan/extra/knowledge")[0], 400)

    def test_inbox_full_counts_do_not_use_latest_fifty_agent_limit(self):
        status, data = self.call("GET", "/api/writing-inbox?kind=agent&limit=25")
        self.assertEqual(status, 200)
        self.assertEqual(data["counts"]["agent"], 56)
        self.assertEqual(data["total"], 56)
        self.assertEqual(len(data["items"]), 25)
        ids = []
        for offset in (0, 25, 50):
            page = self.call("GET", f"/api/writing-inbox?kind=agent&limit=25&offset={offset}")[1]
            ids.extend(item["ref_id"] for item in page["items"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(self.agents))

    def test_focused_agent_opens_an_old_record_outside_the_latest_fifty(self):
        latest = self.call("GET", "/api/writing-agent")[1]["sessions"]
        self.assertEqual(len(latest), 50)
        old_id = self.agents[0]
        self.assertNotIn(old_id, {row["id"] for row in latest})
        status, data = self.call("GET", "/api/writing-agent?session_id=" + old_id)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in data["sessions"]], [old_id])

    def test_focused_agent_scope_and_bad_identity(self):
        old_id = self.agents[0]
        self.assertEqual(self.call("GET", "/api/writing-agent?session_id=" + old_id, project=self.other)[0], 404)
        for query in ("session_id=", "session_id=bad.id", "session_id=x&session_id=y"):
            self.assertEqual(self.call("GET", "/api/writing-agent?" + query)[0], 400)

    def test_inbox_rejects_bad_filters_instead_of_returning_zero(self):
        for query in ("kind=unknown", "kind=", "limit=0", "limit=101", "limit=bad",
                      "offset=-1", "offset=1.5", "offset=", "kind=agent&kind=queue"):
            with self.subTest(query=query):
                status, data = self.call("GET", "/api/writing-inbox?" + query)
                self.assertEqual(status, 400)
                self.assertNotIn("total_all", data)

    def test_other_project_does_not_count_this_project_tasks(self):
        status, data = self.call("GET", "/api/writing-inbox", project=self.other)
        self.assertEqual(status, 200)
        self.assertEqual(data["counts"]["agent"], 0)
        self.assertFalse(set(self.agents) & {row["ref_id"] for row in data["items"]})

    def test_only_public_frontend_files_are_served(self):
        for asset in ("knowledge-workspace.js", "writing-inbox.js", "knowledge-workspace.css",
                      "editor-preferences.css"):
            with urllib.request.urlopen(self.base + "/" + asset) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), (ROOT / asset).read_bytes())
        for private in ("moyu_knowledge.py", "moyu_inbox.py", "moyu.sqlite3", ".env"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + "/" + private)
            self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
