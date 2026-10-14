"""Real HTTP guards for POV guidance changed by live names and aliases."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server
from moyu_provider import ProviderError


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Provider(BaseHTTPRequestHandler):
    requests = []
    total_calls = 0

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(payload)
        type(self).total_calls += 1
        raw = encode({
            "model": "guidance-alias-fixture",
            "choices": [{"message": {"content": "雨水顺着窗框淌下，她松开抵着铜扣的手。"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110},
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


class App(server.Handler):
    def log_message(self, *_args):
        pass


class GuidanceAliasHTTP(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="moyu-guidance-alias-")
        self.environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_SECRET_KEY": "",
            "MOYU_CREDENTIALS_DIR": str(Path(self.temporary.name) / "credentials"),
        })
        self.environment.start()
        server.DB_PATH = Path(self.temporary.name) / "book.sqlite3"
        server.init_db()
        Provider.requests = []
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), App)
        for instance in (self.provider, self.http):
            threading.Thread(target=instance.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        self.assertEqual(self.call("GET", "/api/state")[0], 200)
        status, settings = self.call("PUT", "/api/ai/settings", {
            "base_url": f"http://127.0.0.1:{self.provider.server_port}/v1",
            "model": "guidance-alias-fixture", "api_key": "", "daily_limit": 20,
            "parameters": {"temperature": 0, "max_tokens": 800},
        })
        self.assertEqual(status, 200, settings)
        status, self.person = self.call("POST", "/api/records", {
            "type": "人物", "name": "林岚", "aliases": ["岚"], "description": "守在北窗的人。",
        })
        self.assertEqual(status, 201, self.person)
        blueprint = {
            "premise": "雨夜窗前的等候",
            "volumes": [{
                "id": "alias-volume", "title": "窗前", "summary": "在北窗等待脚步声。",
                "chapters": [{
                    "id": "alias-plan", "title": "等候", "goal": "等待来客", "pov": "岚",
                    "scenes": [{
                        "id": "alias-scene", "title": "窗边等候", "goal": "POV_ALIAS_SCENE_CANARY",
                        "location": "北窗", "pov": "林岚",
                    }],
                }],
            }],
        }
        status, saved = self.call("PUT", "/api/blueprint", {
            "expected_version": 0, "blueprint": blueprint,
        })
        self.assertEqual(status, 200, saved)
        status, built = self.call("POST", "/api/blueprint/materialize", {
            "expected_version": saved["version"],
        })
        self.assertEqual(status, 200, built)
        self.chapter_id = next(item["id"] for item in built["state"]["chapters"]
                               if item.get("plan_id") == "alias-plan")
        self.assertEqual(self.call("PATCH", "/api/chapters/" + self.chapter_id, {
            "body": "她把指尖抵在窗扣上，听见雨水落在石阶。" * 8,
        })[0], 200)
        self.request = {"task": "continue", "chapter_id": self.chapter_id,
                        "instruction": "按本章已保存的场景续写。"}
        self.assertIn("POV_ALIAS_SCENE_CANARY", self.preview()["user"])

    def tearDown(self):
        for instance in (self.http, self.provider):
            instance.shutdown()
            instance.server_close()
        self.environment.stop()
        self.temporary.cleanup()

    def call(self, method, route, payload=None, project="default-project"):
        import urllib.error
        import urllib.request
        request = urllib.request.Request(
            self.base + route, method=method,
            data=encode(payload) if payload is not None else None,
            headers={"Content-Type": "application/json", "X-Moyu-Project": project},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def preview(self):
        status, preview = self.call("POST", "/api/ai/prompt-preview", self.request)
        self.assertEqual(status, 200, preview)
        return preview

    def queue(self):
        status, job = self.call("POST", "/api/ai/queue", {**self.request, "defer": True})
        self.assertEqual(status, 202, job)
        return job

    def process(self, job):
        status, completed = self.call("POST", "/api/ai/jobs/" + job["id"] + "/process", {})
        self.assertEqual(status, 200, completed)
        return completed

    def edit(self, **fields):
        status, record = self.call("PATCH", "/api/records/" + self.person["id"], fields)
        self.assertEqual(status, 200, record)
        return record

    def body(self):
        status, state = self.call("GET", "/api/state")
        self.assertEqual(status, 200, state)
        return next(item["body"] for item in state["chapters"] if item["id"] == self.chapter_id)

    def ledger(self):
        with server.connect() as conn:
            return tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                         for table in ("app_ai_job", "ai_run", "app_quota_reservation",
                                       "app_candidate_application", "document_revision"))

    def assert_dispatch_blocked(self, job):
        previous_body, previous_calls = self.body(), len(Provider.requests)
        result = self.process(job)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("指南", result.get("error", ""))
        self.assertEqual(len(Provider.requests), previous_calls)
        self.assertEqual(self.body(), previous_body)

    def test_removed_alias_blocks_queued_dispatch(self):
        job = self.queue()
        self.edit(aliases=[])
        self.assertNotIn("POV_ALIAS_SCENE_CANARY", self.preview()["user"])
        self.assert_dispatch_blocked(job)

    def test_ambiguous_alias_blocks_queued_dispatch(self):
        job = self.queue()
        status, other = self.call("POST", "/api/records", {
            "type": "人物", "name": "铁鹭", "aliases": ["岚"], "description": "另一位来客。",
        })
        self.assertEqual(status, 201, other)
        self.assertNotIn("POV_ALIAS_SCENE_CANARY", self.preview()["user"])
        self.assert_dispatch_blocked(job)

    def test_renamed_person_blocks_queued_dispatch(self):
        job = self.queue()
        self.edit(name="林青")
        self.assert_dispatch_blocked(job)

    def test_archived_person_blocks_queued_dispatch(self):
        job = self.queue()
        status, result = self.call("DELETE", "/api/records/" + self.person["id"], {})
        self.assertEqual(status, 200, result)
        self.assertNotIn("POV_ALIAS_SCENE_CANARY", self.preview()["user"])
        self.assert_dispatch_blocked(job)

    def test_changed_type_invalidates_guidance_dependency(self):
        job = self.queue()
        self.edit(type="设定")
        self.assert_dispatch_blocked(job)

    def test_removed_alias_blocks_completed_candidate_apply(self):
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.edit(aliases=[])
        self.assertNotIn("POV_ALIAS_SCENE_CANARY", self.preview()["user"])
        ledger, body = self.ledger(), self.body()
        status, rejected = self.call("POST", "/api/ai/runs/" + job["ai_run_id"] + "/apply", {
            **job["source"], "decision": "accepted",
            "replacement": "\n\n" + completed["result"]["text"],
        })
        self.assertEqual(status, 400, rejected)
        self.assertIn("指南", rejected.get("error", ""))
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), 1)

    def test_removed_alias_blocks_failed_job_and_run_retry(self):
        job = self.queue()
        failure = ProviderError("fixture did not dispatch", code="fixture_not_dispatched",
                                request_dispatched=False)
        with patch("server.call_ai_provider", side_effect=failure):
            completed = self.process(job)
        self.assertEqual(completed["status"], "failed", completed)
        self.assertEqual(completed["result"]["billing_state"], "not_dispatched")
        self.edit(aliases=[])
        self.assertNotIn("POV_ALIAS_SCENE_CANARY", self.preview()["user"])
        ledger, body = self.ledger(), self.body()
        for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
            status, rejected = self.call("POST", f"/api/ai/{kind}/{identifier}/retry",
                                         {"mode": "original"})
            self.assertEqual(status, 409, (kind, rejected))
            self.assertIn("指南", rejected.get("error", ""))
            self.assertEqual(self.ledger(), ledger)
            self.assertEqual(self.body(), body)
            self.assertEqual(len(Provider.requests), 0)

    def test_consumed_description_edit_preserves_alias_but_blocks_old_materials(self):
        job = self.queue()
        body = self.body()
        self.edit(description="仍在北窗守候，铜扣抵着她的掌心。")
        self.assertIn("POV_ALIAS_SCENE_CANARY", self.preview()["user"])
        completed = self.process(job)
        self.assertEqual(completed["status"], "failed", completed)
        self.assertIn("资料", completed.get("error", ""))
        self.assertEqual(completed["result"]["billing_state"], "not_dispatched")
        self.assertEqual(len(Provider.requests), 0)
        self.assertEqual(self.body(), body)

    def test_omitted_description_edit_keeps_alias_guidance_usable(self):
        self.request["material_controls"] = {"records": {self.person["id"]: {"omit": True}}}
        self.assertNotIn("守在北窗的人。", self.preview()["user"])
        job = self.queue()
        self.edit(description="未消费资料的新描述。")
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        sent = json.dumps(Provider.requests[0], ensure_ascii=False)
        self.assertIn("POV_ALIAS_SCENE_CANARY", sent)
        self.assertNotIn("未消费资料的新描述。", sent)

    def test_other_project_alias_does_not_invalidate_this_guidance(self):
        job = self.queue()
        status, created = self.call("POST", "/api/projects", {"title": "另一部隔离作品"})
        self.assertEqual(status, 201, created)
        status, other = self.call("POST", "/api/records", {
            "type": "人物", "name": "别作人物", "aliases": ["岚"], "description": "另一部作品的角色。",
        }, created["project"]["id"])
        self.assertEqual(status, 201, other)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertIn("POV_ALIAS_SCENE_CANARY", json.dumps(Provider.requests[0], ensure_ascii=False))

    def test_new_request_after_removed_alias_uses_current_filtered_guidance(self):
        self.edit(aliases=[])
        self.assertNotIn("POV_ALIAS_SCENE_CANARY", self.preview()["user"])
        job = self.queue()
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertNotIn("POV_ALIAS_SCENE_CANARY",
                         json.dumps(Provider.requests[0], ensure_ascii=False))


class ReportResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({"name": test.id().rsplit(".", 1)[-1], "passed": True})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.cases.append({"name": test.id().rsplit(".", 1)[-1], "passed": False,
                           "error": self._exc_info_to_string(error, test)})

    def addError(self, test, error):
        super().addError(test, error)
        self.cases.append({"name": test.id().rsplit(".", 1)[-1], "passed": False,
                           "error": self._exc_info_to_string(error, test)})


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        "moyu_workspace.py", "moyu_context.py", "server.py",
        "tests/guidance_alias_http_fixtures.py",
    )}


def main():
    started = datetime.now(timezone.utc).isoformat()
    before = fingerprints()
    Provider.total_calls = 0
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GuidanceAliasHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=ReportResult).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful(),
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "checks": result.testsRun, "cases": result.cases,
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "scope": "isolated temporary database; real HTTP; synthetic POV scenes; free local provider",
    }
    output = os.environ.get("MOYU_GUIDANCE_ALIAS_REPORT")
    if output:
        target = Path(output)
        if target.exists():
            raise RuntimeError("Evidence already exists; select a new report path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
