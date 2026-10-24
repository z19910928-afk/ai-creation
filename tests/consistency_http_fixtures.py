#!/usr/bin/env python3
"""HTTP contract for the read-only consistency report.

Run: python tests/consistency_http_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import server  # noqa: E402

PROJECT = "synthetic-consistency-book"
BRANCH = "synthetic-consistency-branch"
ACTOR = "synthetic-consistency-author"
CATEGORIES = {
    "fact", "timeline", "knowledge", "attribute", "relation", "foreshadow", "evidence",
}
SEVERITIES = {"blocker", "high", "medium", "low"}


class QuietHandler(server.Handler):
    def log_message(self, *_args):
        pass


class ConsistencyHTTP(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.temp = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="moyu-consistency-http-")))
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
    def get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def table_counts(self) -> dict[str, int]:
        with server.connect() as conn:
            names = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {
                name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                for name in names
            }

    def add_contradiction(self) -> tuple[str, str]:
        """同一实体、同一谓词、重叠有效期、两个互斥取值。"""
        with server.connect() as conn:
            entity = conn.execute(
                "SELECT id FROM entity WHERE project_id=? ORDER BY id LIMIT 1", (PROJECT,)
            ).fetchone()
            self.assertIsNotNone(entity, "seed 必须提供至少一个实体")
            entity_id = entity[0]
            span = conn.execute(
                "SELECT id FROM source_span WHERE project_id=? AND status='valid' "
                "ORDER BY id LIMIT 1", (PROJECT,)
            ).fetchone()
            evidence = [{"source_span_id": span[0]}] if span else [{"note": "synthetic"}]
            stamp = server.now()
            for identifier, value in (("cx-http-a", "青云城"), ("cx-http-b", "西域")):
                conn.execute(
                    "INSERT INTO assertion(id,project_id,branch_id,subject_entity_id,"
                    "predicate,object_json,epistemic_mode,state,confidence,evidence_json,"
                    "reviewed_by,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, PROJECT, BRANCH, entity_id, "located_in",
                     json.dumps({"value": value}, ensure_ascii=False),
                     "world_truth", "canon", 1.0, json.dumps(evidence, ensure_ascii=False),
                     "本地作者", stamp),
                )
            return entity_id, json.dumps(evidence, ensure_ascii=False)

    # -- tests ------------------------------------------------------------ #
    def test_report_contract(self):
        status, body = self.get("/api/consistency")
        self.assertEqual(status, 200)
        self.assertEqual(body["engine"], "moyu-consistency")
        self.assertIsInstance(body["version"], int)
        self.assertEqual(body["project_id"], PROJECT)
        self.assertEqual(body["branch_id"], BRANCH)
        self.assertIsInstance(body["score"], (int, float))
        self.assertGreaterEqual(body["score"], 0)
        self.assertLessEqual(body["score"], 100)
        self.assertIn(body["grade"], {"A", "B", "C", "D"})
        self.assertEqual(set(body["categories"]), CATEGORIES)
        self.assertEqual(set(body["examined"]), CATEGORIES)
        self.assertEqual(body["total_examined"], sum(body["examined"].values()))
        self.assertEqual(body["returned"], len(body["findings"]))
        self.assertIsNone(body["filtered_by"])
        self.assertIsInstance(body["summary"], str)
        datetime.fromisoformat(body["generated_at"])
        for item in body["findings"]:
            self.assertIn(item["category"], CATEGORIES)
            self.assertIn(item["severity"], SEVERITIES)
            self.assertTrue(item["id"].startswith("cx_"))
            self.assertTrue(item["detail"])

    def test_contradiction_is_reported_and_lowers_score(self):
        _, baseline = self.get("/api/consistency")
        entity_id, _ = self.add_contradiction()
        status, body = self.get("/api/consistency")
        self.assertEqual(status, 200)
        self.assertLess(body["score"], baseline["score"], "新增矛盾必须降低分数")
        self.assertGreater(body["counts"]["high"], 0)
        fact = [item for item in body["findings"] if item["category"] == "fact"]
        self.assertTrue(fact, "必须报出事实矛盾")
        matched = [item for item in fact
                   if set(item["evidence"].get("assertion_ids", [])) >= {"cx-http-a", "cx-http-b"}]
        self.assertTrue(matched, "证据必须能回到两条冲突断言")
        self.assertEqual(matched[0]["evidence"]["entity_id"], entity_id)

    def test_min_severity_filters_without_inflating_score(self):
        self.add_contradiction()
        _, full = self.get("/api/consistency")
        status, filtered = self.get("/api/consistency?min_severity=high")
        self.assertEqual(status, 200)
        self.assertEqual(filtered["score"], full["score"], "筛选不得美化分数")
        self.assertEqual(filtered["counts"], full["counts"])
        self.assertEqual(filtered["filtered_by"], "high")
        self.assertLessEqual(filtered["returned"], len(full["findings"]))
        for item in filtered["findings"]:
            self.assertIn(item["severity"], {"blocker", "high"})

    def test_bad_filters_are_rejected(self):
        status, body = self.get("/api/consistency?min_severity=bogus")
        self.assertEqual(status, 400)
        self.assertTrue("min_severity" in body["error"] or "严重度" in body["error"], body["error"])
        status, body = self.get("/api/consistency?min_severity=high&min_severity=low")
        self.assertEqual(status, 400)
        self.assertIn("只能填写一次", body["error"])

    def test_contradiction_reaches_publication_gate(self):
        with server.connect() as conn:
            clean = server.export_finalization_issues(conn)
        self.assertFalse([item for item in clean if "一致性阻断" in item],
                         "干净项目不应出现一致性阻断")
        self.add_contradiction()
        with server.connect() as conn:
            issues = server.export_finalization_issues(conn)
        self.assertTrue([item for item in issues if "一致性阻断" in item],
                        f"事实矛盾必须进入发表门禁：{issues}")

    def test_report_is_read_only(self):
        self.get("/api/consistency")  # 让 seed 先落库
        before = self.table_counts()
        self.get("/api/consistency")
        self.get("/api/consistency?min_severity=low")
        self.assertEqual(self.table_counts(), before, "一致性报告不得写库")


if __name__ == "__main__":
    unittest.main(verbosity=2)
