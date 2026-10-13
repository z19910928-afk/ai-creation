#!/usr/bin/env python3
"""Real HTTP staged planning with an isolated DB and free local provider."""
from __future__ import annotations

from collections import Counter
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import moyu_backups as backups  # noqa: E402
import moyu_planning as planning  # noqa: E402


MANUSCRIPT = "😀作者已经写下的正文。\n林岚握着钥匙离开渡口，这一段不能被规划覆盖。"
INITIAL = {
    "premise": "雨夜码头的第二把锁", "genre": "悬疑", "audience": "成年读者",
    "tone": "克制", "contract": "人物只依据自己知道的事实行动",
    "counts": {"volumes": 1, "chapters_per_volume": 1, "scenes_per_chapter": 1},
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def candidate_for(stage, blueprint):
    if stage == "overview":
        return {
            **{key: blueprint[key] for key in ("genre", "audience", "tone", "contract")},
            "premise": blueprint["premise"] + "；林岚需要决定是否交出第二把钥匙",
        }
    if stage == "volumes":
        return {"volumes": [
            {"id": volume["id"], "title": f"第{index}卷 渡口选择",
             "summary": "守灯人隐瞒锁的用途，林岚查明钥匙去向。"}
            for index, volume in enumerate(blueprint["volumes"], 1)
        ]}
    if stage == "chapters":
        return {"volumes": [
            {"id": volume["id"], "chapters": [
                {"id": chapter["id"], "title": f"第{index}章 旧锁",
                 "goal": "辨认第二把钥匙", "pov": "林岚", "hook": "门后响起拖链声"}
                for index, chapter in enumerate(volume["chapters"], 1)
            ]}
            for volume in blueprint["volumes"]
        ]}
    return {"volumes": [
        {"id": volume["id"], "chapters": [
            {"id": chapter["id"], "scenes": [
                {"id": scene["id"], "title": f"场景{index} 仓库门外",
                 "goal": "取回月牙钥匙", "location": "码头仓库", "pov": "林岚"}
                for index, scene in enumerate(chapter["scenes"], 1)
            ]}
            for chapter in volume["chapters"]
        ]}
        for volume in blueprint["volumes"]
    ]}


class LocalPlanningProvider(BaseHTTPRequestHandler):
    calls = []
    fail_once = set()
    unknown_once = set()
    wrong_ids_once = set()
    lock = threading.Lock()

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        user = payload["messages"][-1]["content"]
        context = json.loads(user.split("<manuscript>\n", 1)[1].split("\n</manuscript>", 1)[0])
        stage = context["planning_workflow"]["stage"]
        with self.lock:
            self.calls.append({"stage": stage, "context": context, "payload": payload})
            fail = stage in self.fail_once
            unknown = stage in self.unknown_once
            wrong_ids = stage in self.wrong_ids_once
            self.fail_once.discard(stage)
            self.unknown_once.discard(stage)
            self.wrong_ids_once.discard(stage)
        if unknown:
            self.send_response(429)
            body = encode({"error": {"message": "本机固定费用未知状态", "type": "rate_limit"}})
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        candidate = candidate_for(stage, context["blueprint"])
        if fail:
            candidate["volumes"][0]["chapters"][0]["goal"] = False
        if wrong_ids:
            candidate["volumes"][0]["id"] = "wrong-volume-id"
        self.send_response(200)
        body = encode({
            "model": "local-planning-fixture",
            "choices": [{"message": {"content": json.dumps(candidate, ensure_ascii=False)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 60, "completion_tokens": 45, "total_tokens": 105},
        })
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class PlanningHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db = server.DB_PATH
        cls.provider = ThreadingHTTPServer(("127.0.0.1", 0), LocalPlanningProvider)
        cls.provider.daemon_threads = True
        cls.provider_thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.provider_thread.start()
        cls.provider_base = f"http://127.0.0.1:{cls.provider.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.provider_thread.join(timeout=3)
        server.DB_PATH = cls.original_db

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="moyu-planning-http-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
            "MOYU_AI_DAILY_QUOTA": "0", "MOYU_CREDENTIALS_DIR": str(self.root / "private-keys"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        server.DB_PATH = self.root / "book.sqlite3"
        server.init_db()
        self.project = server.PROJECT_ID
        self.branch = server.BRANCH_ID
        with LocalPlanningProvider.lock:
            LocalPlanningProvider.calls.clear()
            LocalPlanningProvider.fail_once.clear()
            LocalPlanningProvider.unknown_once.clear()
            LocalPlanningProvider.wrong_ids_once.clear()

        class AppHandler(server.Handler):
            def log_message(self, *_args):
                return

        self.app = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        self.app.daemon_threads = True
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_app)
        self.base = f"http://127.0.0.1:{self.app.server_port}"
        self.chapter_id = self.state()["chapters"][0]["id"]
        status, changed = self.request("PATCH", "/api/chapters/" + self.chapter_id, {"body": MANUSCRIPT})
        self.assertEqual(status, 200, changed)
        self.manuscript = self.manuscript_snapshot()
        source = self.blueprint()
        source["blueprint"]["premise"] = "作者已有的独立故事蓝图"
        status, saved = self.request("PUT", "/api/blueprint", {
            "expected_version": source["version"], "blueprint": source["blueprint"],
        })
        self.assertEqual(status, 200, saved)
        self.source_blueprint = saved
        status, settings = self.request("PUT", "/api/ai/settings", {
            "base_url": self.provider_base, "model": "local-planning-fixture",
            "api_key": "", "daily_limit": 20,
            "parameters": {"temperature": 0, "max_tokens": 4000},
        })
        self.assertEqual(status, 200, settings)

    def close_app(self):
        self.app.shutdown()
        self.app.server_close()
        self.thread.join(timeout=3)

    def request(self, method, route, payload=None, *, project=None, branch=None):
        request = urllib.request.Request(
            self.base + route, method=method,
            data=encode(payload) if payload is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Moyu-Project": project or self.project,
                "X-Moyu-Branch": branch or self.branch,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def state(self):
        status, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200, state)
        return state

    def manuscript_snapshot(self):
        with server.connect() as conn:
            return tuple(tuple(row) for row in conn.execute(
                "SELECT r.id,r.document_node_id,r.canonical_text FROM document_revision r "
                "JOIN document_node n ON n.id=r.document_node_id "
                "WHERE n.project_id=? AND n.branch_id=? ORDER BY r.rowid",
                (self.project, self.branch)))

    def blueprint(self):
        status, blueprint = self.request("GET", "/api/blueprint")
        self.assertEqual(status, 200, blueprint)
        return blueprint

    def queue_snapshot(self):
        with server.connect() as conn:
            counts = tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                           for table in ("app_ai_job", "ai_run", "app_quota_reservation", "app_planning_attempt"))
        with LocalPlanningProvider.lock:
            calls = len(LocalPlanningProvider.calls)
        return counts, calls

    def workflow(self, **initial):
        status, created = self.request("POST", "/api/planning-workflows", {
            "initial": {**copy.deepcopy(INITIAL), **initial},
        })
        self.assertEqual(status, 201, created)
        result = created.get("workflow", created)
        self.assertEqual(result["stage"], "overview")
        self.assertEqual(result["source_version"], self.source_blueprint["version"])
        return result

    def detail(self, workflow):
        status, result = self.request("GET", "/api/planning-workflows/" + workflow["id"])
        self.assertEqual(status, 200, result)
        return result.get("workflow", result)

    def start_payload(self, workflow):
        return {"expected_stage": workflow["stage"], "expected_version": workflow["version"], "defer": True}

    def start(self, workflow):
        status, started = self.request(
            "POST", "/api/planning-workflows/" + workflow["id"] + "/start", self.start_payload(workflow),
        )
        self.assertEqual(status, 202, started)
        result = started.get("workflow", started)
        self.assertEqual(result["attempt"]["status"], "queued")
        return result

    def process(self, workflow, *, expected="success"):
        job_id = workflow["attempt"]["job_id"]
        status, completed = self.request("POST", f"/api/ai/jobs/{job_id}/process", {})
        self.assertEqual(status, 200, completed)
        self.assertEqual(completed["status"], expected, completed)
        return self.detail(workflow)

    def confirm_payload(self, workflow):
        return {
            "expected_stage": workflow["stage"], "expected_version": workflow["version"],
            "attempt_id": workflow["attempt"]["id"], "candidate_hash": workflow["attempt"]["candidate_hash"],
        }

    def confirm(self, workflow):
        self.assertEqual(workflow["attempt"]["status"], "complete")
        status, result = self.request(
            "POST", "/api/planning-workflows/" + workflow["id"] + "/confirm", self.confirm_payload(workflow),
        )
        self.assertEqual(status, 200, result)
        return result.get("workflow", result)

    def complete_stage(self, workflow):
        return self.confirm(self.process(self.start(workflow)))

    def finish(self, workflow):
        for stage in planning.STAGES:
            self.assertEqual(workflow["stage"], stage)
            workflow = self.complete_stage(workflow)
            self.assertEqual(self.manuscript_snapshot(), self.manuscript)
            self.assertEqual(self.blueprint(), self.source_blueprint)
        self.assertEqual(workflow["status"], "completed")
        self.assertEqual(workflow["stage_index"], 4)
        return workflow

    def apply(self, workflow):
        return self.request("POST", "/api/planning-workflows/" + workflow["id"] + "/apply", {
            "expected_source_version": workflow["source_version"],
        })

    def change_blueprint(self):
        current = self.blueprint()
        payload = copy.deepcopy(current["blueprint"])
        payload["premise"] = "作者在规划之外重新确定故事核心"
        status, changed = self.request("PUT", "/api/blueprint", {
            "expected_version": current["version"], "blueprint": payload,
        })
        self.assertEqual(status, 200, changed)
        return changed

    def test_workflows_are_isolated_by_project_and_branch(self):
        workflow = self.workflow()
        status, created = self.request("POST", "/api/projects", {"title": "另一本独立作品", "goal": 50000})
        self.assertEqual(status, 201, created)
        other = created["project"]
        other_id = other["id"]
        other_branch = created["state"]["branch_id"]
        status, listing = self.request("GET", "/api/planning-workflows", project=other_id, branch=other_branch)
        self.assertEqual(status, 200, listing)
        rows = listing.get("workflows", listing.get("items", []))
        self.assertNotIn(workflow["id"], {item["id"] for item in rows})
        snapshot = self.queue_snapshot()
        for method, suffix, payload in (
            ("GET", "", None), ("POST", "/start", self.start_payload(workflow)),
            ("POST", "/confirm", {"expected_stage": "overview", "attempt_id": "foreign-attempt",
                                  "candidate_hash": "foreign-hash", "expected_version": 0}),
            ("POST", "/cancel", {}),
            ("POST", "/apply", {"expected_source_version": workflow["source_version"]}),
        ):
            with self.subTest(route=suffix):
                status, rejected = self.request(
                    method, "/api/planning-workflows/" + workflow["id"] + suffix, payload,
                    project=other_id, branch=other_branch,
                )
                self.assertEqual(status, 404, rejected)
                self.assertEqual(self.queue_snapshot(), snapshot)
        status, rejected = self.request("GET", "/api/planning-workflows/" + workflow["id"],
                                        branch=other_branch)
        self.assertEqual(status, 404, rejected)
        self.assertEqual(self.detail(workflow)["status"], "active")

    def test_duplicate_start_and_unconfirmed_start_do_not_duplicate_jobs_or_reservations(self):
        workflow = self.workflow()
        payload = self.start_payload(workflow)
        started = self.start(workflow)
        snapshot = self.queue_snapshot()
        self.assertEqual(snapshot, ((1, 1, 1, 1), 0))
        status, rejected = self.request(
            "POST", "/api/planning-workflows/" + workflow["id"] + "/start", payload,
        )
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.queue_snapshot(), snapshot)
        complete = self.process(started)
        snapshot = self.queue_snapshot()
        status, rejected = self.request(
            "POST", "/api/planning-workflows/" + workflow["id"] + "/start", self.start_payload(complete),
        )
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.queue_snapshot(), snapshot)
        self.assertEqual(self.detail(workflow)["attempt"]["status"], "complete")

    def test_concurrent_start_creates_one_job_and_one_reservation(self):
        workflow = self.workflow()
        barrier = threading.Barrier(3)
        responses = []
        errors = []

        def submit():
            try:
                barrier.wait(timeout=5)
                responses.append(self.request(
                    "POST", "/api/planning-workflows/" + workflow["id"] + "/start",
                    self.start_payload(workflow),
                ))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=25)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(status for status, _ in responses), [202, 409], responses)
        self.assertEqual(self.queue_snapshot(), ((1, 1, 1, 1), 0))

    def test_task_detail_and_queue_expose_only_the_planning_identity(self):
        workflow = self.start(self.workflow())
        job_id = workflow["attempt"]["job_id"]
        status, detail = self.request("GET", "/api/ai/jobs/" + job_id)
        self.assertEqual(status, 200, detail)
        status, queue = self.request("GET", "/api/ai/queue")
        self.assertEqual(status, 200, queue)
        queued = next(job for job in queue["jobs"] if job["id"] == job_id)
        for job in (detail, queued):
            self.assertEqual(job["planning_workflow"]["id"], workflow["id"])
            self.assertEqual(job["planning_workflow"]["stage"], "overview")
            self.assertEqual(job["planning_workflow"]["attempt_id"], workflow["attempt"]["id"])
            self.assertNotIn("context_json", job)
            self.assertNotIn("instruction", job)
            self.assertNotIn(INITIAL["premise"], json.dumps(job, ensure_ascii=False))
        self.assertEqual(self.queue_snapshot(), ((1, 1, 1, 1), 0))

    def test_four_stages_preserve_confirmations_and_only_final_apply_updates_blueprint(self):
        workflow = self.finish(self.workflow())
        self.assertEqual(len(workflow["attempts"]), 4)
        self.assertEqual({attempt["status"] for attempt in workflow["attempts"]}, {"confirmed"})
        self.assertEqual(workflow["blueprint"]["premise"], INITIAL["premise"] + "；林岚需要决定是否交出第二把钥匙")
        with LocalPlanningProvider.lock:
            calls = copy.deepcopy(LocalPlanningProvider.calls)
        self.assertEqual([call["stage"] for call in calls], list(planning.STAGES))
        self.assertEqual(calls[1]["context"]["blueprint"]["premise"], workflow["blueprint"]["premise"])
        self.assertEqual(calls[2]["context"]["blueprint"]["volumes"][0]["title"], "第1卷 渡口选择")
        self.assertEqual(calls[3]["context"]["blueprint"]["volumes"][0]["chapters"][0]["goal"], "辨认第二把钥匙")
        for call in calls:
            self.assertNotIn("current_chapter", call["context"])
            self.assertNotIn(MANUSCRIPT, json.dumps(call["context"], ensure_ascii=False))
        status, applied = self.apply(workflow)
        self.assertEqual(status, 200, applied)
        final = self.blueprint()
        self.assertEqual(final["version"], self.source_blueprint["version"] + 1)
        self.assertEqual(final["blueprint"], workflow["final_blueprint"])
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)
        snapshot = self.queue_snapshot()
        status, repeated = self.apply(workflow)
        self.assertEqual(status, 200, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.blueprint(), final)
        self.assertEqual(self.queue_snapshot(), snapshot)
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)

    def test_third_stage_failure_retries_only_that_stage_and_preserves_confirmed_stages(self):
        workflow = self.complete_stage(self.complete_stage(self.workflow()))
        self.assertEqual(workflow["stage"], "chapters")
        earlier = copy.deepcopy(workflow["attempts"])
        confirmed_blueprint = copy.deepcopy(workflow["blueprint"])
        with LocalPlanningProvider.lock:
            LocalPlanningProvider.fail_once.add("chapters")
        failed = self.process(self.start(workflow), expected="failed")
        self.assertEqual(failed["stage"], "chapters")
        self.assertEqual(failed["attempt"]["status"], "failed")
        self.assertEqual(failed["blueprint"], confirmed_blueprint)
        for attempt in earlier:
            self.assertEqual(next(item for item in failed["attempts"] if item["id"] == attempt["id"]), attempt)
        persisted = self.detail(failed)
        self.assertEqual(persisted["blueprint"], confirmed_blueprint)
        retry = self.start(persisted)
        self.assertEqual(retry["attempt"]["attempt_no"], 2)
        self.assertNotEqual(retry["attempt"]["id"], failed["attempt"]["id"])
        workflow = self.complete_stage(self.confirm(self.process(retry)))
        self.assertEqual(workflow["status"], "completed")
        with LocalPlanningProvider.lock:
            counts = Counter(call["stage"] for call in LocalPlanningProvider.calls)
        self.assertEqual(counts, {"overview": 1, "volumes": 1, "chapters": 2, "scenes": 1})
        self.assertEqual(self.blueprint(), self.source_blueprint)
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)
        status, applied = self.apply(workflow)
        self.assertEqual(status, 200, applied)
        self.assertEqual(self.blueprint()["blueprint"], workflow["final_blueprint"])
        status, failed_job = self.request("GET", "/api/ai/jobs/" + failed["attempt"]["job_id"])
        self.assertEqual(status, 200, failed_job)
        self.assertEqual(failed_job["status"], "failed")
        self.assertEqual(failed_job["author_decision"], "pending")
        for attempt in workflow["attempts"]:
            if attempt["status"] == "confirmed":
                status, job = self.request("GET", "/api/ai/jobs/" + attempt["job_id"])
                self.assertEqual(status, 200, job)
                self.assertEqual(job["author_decision"], "accepted")
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)

    def test_external_blueprint_change_blocks_stage_start_confirmation_and_final_application(self):
        for point in ("start", "confirm", "apply"):
            with self.subTest(point=point):
                self.source_blueprint = self.blueprint()
                workflow = self.workflow()
                if point == "confirm":
                    workflow = self.process(self.start(workflow))
                elif point == "apply":
                    workflow = self.finish(workflow)
                changed = self.change_blueprint()
                snapshot = self.queue_snapshot()
                if point == "start":
                    status, rejected = self.request(
                        "POST", "/api/planning-workflows/" + workflow["id"] + "/start", self.start_payload(workflow),
                    )
                elif point == "confirm":
                    status, rejected = self.request(
                        "POST", "/api/planning-workflows/" + workflow["id"] + "/confirm", self.confirm_payload(workflow),
                    )
                else:
                    status, rejected = self.apply(workflow)
                self.assertEqual(status, 409, rejected)
                self.assertEqual(self.blueprint(), changed)
                self.assertEqual(self.queue_snapshot(), snapshot)
                self.assertEqual(self.manuscript_snapshot(), self.manuscript)

    def test_source_changes_while_queued_block_provider_dispatch_and_release_reservation(self):
        workflow = self.start(self.workflow())
        changed = self.change_blueprint()
        workflow = self.process(workflow, expected="failed")
        self.assertEqual(workflow["attempt"]["status"], "failed")
        status, job = self.request("GET", "/api/ai/jobs/" + workflow["attempt"]["job_id"])
        self.assertEqual(status, 200, job)
        self.assertEqual(job["result"]["billing_state"], "not_dispatched")
        with server.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation WHERE ai_run_id=?", (job["ai_run_id"],)).fetchone()[0],
                "released")
        with LocalPlanningProvider.lock:
            self.assertEqual(LocalPlanningProvider.calls, [])
        self.assertEqual(self.blueprint(), changed)
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)

    def test_unknown_dispatch_cannot_start_a_second_attempt_or_lose_prior_confirmations(self):
        workflow = self.complete_stage(self.complete_stage(self.workflow()))
        before = copy.deepcopy(workflow["blueprint"])
        with LocalPlanningProvider.lock:
            LocalPlanningProvider.unknown_once.add("chapters")
        workflow = self.process(self.start(workflow), expected="failed")
        self.assertEqual(workflow["attempt"]["status"], "unknown")
        self.assertEqual(workflow["blueprint"], before)
        snapshot = self.queue_snapshot()
        status, rejected = self.request(
            "POST", "/api/planning-workflows/" + workflow["id"] + "/start", self.start_payload(workflow),
        )
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.queue_snapshot(), snapshot)
        self.assertEqual(self.detail(workflow)["attempt"]["status"], "unknown")
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)

    def test_cancel_queued_stage_releases_reservation_without_dispatch_or_losing_prior_confirmation(self):
        workflow = self.complete_stage(self.workflow())
        confirmed = copy.deepcopy(workflow["blueprint"])
        started = self.start(workflow)
        with LocalPlanningProvider.lock:
            calls = len(LocalPlanningProvider.calls)
        status, canceled = self.request("POST", "/api/planning-workflows/" + workflow["id"] + "/cancel", {})
        self.assertEqual(status, 200, canceled)
        canceled = canceled.get("workflow", canceled)
        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(canceled["attempt"]["status"], "canceled")
        self.assertEqual(canceled["blueprint"], confirmed)
        status, repeated = self.request("POST", "/api/planning-workflows/" + workflow["id"] + "/cancel", {})
        self.assertEqual(status, 200, repeated)
        status, job = self.request("GET", "/api/ai/jobs/" + started["attempt"]["job_id"])
        self.assertEqual(status, 200, job)
        self.assertEqual(job["status"], "canceled")
        self.assertEqual(job["result"]["billing_state"], "not_dispatched")
        with server.connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM app_quota_reservation WHERE ai_run_id=?", (job["ai_run_id"],)).fetchone()[0],
                "released")
        with LocalPlanningProvider.lock:
            self.assertEqual(len(LocalPlanningProvider.calls), calls)
        snapshot = self.queue_snapshot()
        status, rejected = self.request(
            "POST", "/api/planning-workflows/" + workflow["id"] + "/start", self.start_payload(canceled),
        )
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.queue_snapshot(), snapshot)
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)

    def test_wrong_confirmation_identity_cannot_advance_or_write_any_data(self):
        workflow = self.process(self.start(self.workflow()))
        snapshot = self.queue_snapshot()
        blueprint = self.blueprint()
        for field, value in (
            ("expected_stage", "scenes"), ("expected_version", workflow["version"] + 1),
            ("attempt_id", "wrong-attempt"), ("candidate_hash", "wrong-candidate-hash"),
        ):
            payload = self.confirm_payload(workflow)
            payload[field] = value
            with self.subTest(field=field):
                status, rejected = self.request(
                    "POST", "/api/planning-workflows/" + workflow["id"] + "/confirm", payload,
                )
                self.assertEqual(status, 409, rejected)
                current = self.detail(workflow)
                self.assertEqual(current["stage"], "overview")
                self.assertEqual(current["version"], workflow["version"])
                self.assertEqual(current["attempt"]["status"], "complete")
                self.assertEqual(self.queue_snapshot(), snapshot)
                self.assertEqual(self.blueprint(), blueprint)
                self.assertEqual(self.manuscript_snapshot(), self.manuscript)

    def test_model_changed_stable_ids_fail_without_replacing_confirmed_plan(self):
        workflow = self.complete_stage(self.workflow())
        before = copy.deepcopy(workflow["blueprint"])
        with LocalPlanningProvider.lock:
            LocalPlanningProvider.wrong_ids_once.add("volumes")
        workflow = self.process(self.start(workflow))
        self.assertEqual(workflow["attempt"]["status"], "failed")
        self.assertEqual(workflow["blueprint"], before)
        status, rejected = self.request(
            "POST", "/api/planning-workflows/" + workflow["id"] + "/confirm", self.confirm_payload(workflow),
        )
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.blueprint(), self.source_blueprint)
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)

    def test_archive_restores_confirmed_plan_and_application_without_model_calls(self):
        workflow = self.finish(self.workflow())
        status, applied = self.apply(workflow)
        self.assertEqual(status, 200, applied)
        final_blueprint = self.blueprint()
        status, backup = self.request("POST", "/api/backups", {})
        self.assertEqual(status, 201, backup)
        archive = backups.read(self.root / "backups" / (backup["id"] + ".zip"), self.project)
        tables = archive["payload"]["tables"]
        self.assertEqual(len(tables["app_planning_workflow"]), 1)
        self.assertEqual(len(tables["app_planning_attempt"]), 4)
        self.assertEqual(len(tables["app_planning_application"]), 1)
        self.assertTrue(tables["app_planning_event"])
        with LocalPlanningProvider.lock:
            calls = len(LocalPlanningProvider.calls)
        with patch("server.call_ai_provider", side_effect=AssertionError("restore must not call a provider")) as provider:
            with patch("server.start_ai_worker", side_effect=AssertionError("restore must not start a worker")) as worker:
                status, restored = self.request(
                    "POST", "/api/backups/" + backup["id"] + "/restore", {"confirm": True},
                )
            self.assertEqual(status, 201, restored)
            self.assertFalse(worker.called)
            restored_project = restored["project"]["id"]
            restored_branch = restored["branch_id"]
            restored_workflow = restored["identity_map"][workflow["id"]]
            status, detail = self.request(
                "GET", "/api/planning-workflows/" + restored_workflow,
                project=restored_project, branch=restored_branch,
            )
            self.assertEqual(status, 200, detail)
            self.assertEqual(detail["status"], "canceled")
            self.assertIn("仅供阅读", detail["cancel_reason"])
            self.assertTrue(detail["applied"])
            self.assertEqual(detail["application"]["result_version"], final_blueprint["version"])
            self.assertEqual(detail["final_blueprint"], workflow["final_blueprint"])
            self.assertEqual({attempt["status"] for attempt in detail["attempts"]}, {"confirmed"})
            status, recovered_blueprint = self.request(
                "GET", "/api/blueprint", project=restored_project, branch=restored_branch,
            )
            self.assertEqual(status, 200, recovered_blueprint)
            self.assertEqual(recovered_blueprint, final_blueprint)
            status, drained = self.request(
                "POST", "/api/ai/queue/drain", {}, project=restored_project, branch=restored_branch,
            )
            self.assertEqual(status, 200, drained)
            self.assertFalse(provider.called)
        self.assertEqual(restored["state"]["chapters"][0]["body"], MANUSCRIPT)
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)
        with server.connect() as conn:
            self.assertEqual(backups.capture(conn, self.project), archive["payload"])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchone())
            application = conn.execute(
                "SELECT * FROM app_planning_application WHERE project_id=?", (restored_project,),
            ).fetchone()
            source_application = tables["app_planning_application"][0]
            self.assertEqual(application["workflow_id"], restored_workflow)
            self.assertEqual(application["branch_id"], restored_branch)
            for field in ("source_version", "source_hash", "final_hash", "result_version", "applied_at"):
                self.assertEqual(application[field], source_application[field])
            saved = conn.execute(
                "SELECT source_blueprint_json,final_blueprint_json FROM app_planning_workflow WHERE id=?",
                (restored_workflow,),
            ).fetchone()
            self.assertEqual(application["final_hash"], planning._hash(json.loads(saved["final_blueprint_json"])))
            self.assertEqual(application["source_hash"], planning._hash({
                "version": application["source_version"],
                "blueprint": json.loads(saved["source_blueprint_json"]),
            }))
        with LocalPlanningProvider.lock:
            self.assertEqual(len(LocalPlanningProvider.calls), calls)

    def test_archive_restores_unconfirmed_unknown_and_queued_workflows_without_dispatch(self):
        unconfirmed = self.process(self.start(self.workflow()))
        self.assertEqual(unconfirmed["attempt"]["status"], "complete")
        with LocalPlanningProvider.lock:
            LocalPlanningProvider.unknown_once.add("overview")
        unknown = self.process(self.start(self.workflow()), expected="failed")
        self.assertEqual(unknown["attempt"]["status"], "unknown")
        queued = self.start(self.workflow())
        self.assertEqual(queued["attempt"]["status"], "queued")
        status, backup = self.request("POST", "/api/backups", {})
        self.assertEqual(status, 201, backup)
        archive = backups.read(self.root / "backups" / (backup["id"] + ".zip"), self.project)
        with LocalPlanningProvider.lock:
            calls = len(LocalPlanningProvider.calls)
        with patch("server.call_ai_provider", side_effect=AssertionError("restored planning must not dispatch")) as provider:
            with patch("server.start_ai_worker", side_effect=AssertionError("restored planning must not start a worker")) as worker:
                status, restored = self.request(
                    "POST", "/api/backups/" + backup["id"] + "/restore", {"confirm": True},
                )
                self.assertEqual(status, 201, restored)
                restored_project = restored["project"]["id"]
                restored_branch = restored["branch_id"]
                for source in (unconfirmed, unknown, queued):
                    restored_workflow = restored["identity_map"][source["id"]]
                    status, detail = self.request(
                        "GET", "/api/planning-workflows/" + restored_workflow,
                        project=restored_project, branch=restored_branch,
                    )
                    self.assertEqual(status, 200, detail)
                    self.assertEqual(detail["status"], "canceled")
                    self.assertFalse(detail["applied"])
                    expected_status = "complete" if source["id"] == unconfirmed["id"] else "unknown"
                    self.assertEqual(detail["attempt"]["status"], expected_status)
                    self.assertEqual(detail["attempt"]["candidate_hash"], source["attempt"]["candidate_hash"])
                    self.assertEqual(detail["attempt"]["candidate"], source["attempt"]["candidate"])
                    for suffix, payload in (
                        ("/start", self.start_payload(detail)),
                        ("/confirm", self.confirm_payload(detail)),
                        ("/apply", {"expected_source_version": detail["source_version"]}),
                    ):
                        status, rejected = self.request(
                            "POST", "/api/planning-workflows/" + restored_workflow + suffix, payload,
                            project=restored_project, branch=restored_branch,
                        )
                        self.assertEqual(status, 409, rejected)
                status, drained = self.request(
                    "POST", "/api/ai/queue/drain", {},
                    project=restored_project, branch=restored_branch,
                )
                self.assertEqual(status, 200, drained)
                self.assertFalse(worker.called)
            self.assertFalse(provider.called)
        with server.connect() as conn:
            self.assertEqual(backups.capture(conn, self.project), archive["payload"])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_planning_application WHERE project_id=?", (restored_project,),
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_ai_job WHERE project_id=? AND status IN ('queued','running')",
                (restored_project,),
            ).fetchone()[0], 0)
        self.assertEqual(self.manuscript_snapshot(), self.manuscript)
        self.assertEqual(self.blueprint(), self.source_blueprint)
        with LocalPlanningProvider.lock:
            self.assertEqual(len(LocalPlanningProvider.calls), calls)

    def test_new_planning_materialization_creates_new_chapters_and_preserves_previous_manuscript(self):
        first = self.finish(self.workflow())
        status, applied = self.apply(first)
        self.assertEqual(status, 200, applied)
        status, materialized = self.request(
            "POST", "/api/blueprint/materialize", {"expected_version": applied["version"]},
        )
        self.assertEqual(status, 200, materialized)
        first_plan_ids = {
            chapter["id"] for volume in first["final_blueprint"]["volumes"]
            for chapter in volume["chapters"]
        }
        first_chapter = next(
            chapter for chapter in materialized["state"]["chapters"] if chapter["plan_id"] in first_plan_ids
        )
        status, edited = self.request("PATCH", "/api/chapters/" + first_chapter["id"], {
            "title": "作者旧稿正式标题", "body": "😀第一轮规划已经写好的正文。",
            "chapter_goal": "作者旧稿目标", "scene_plan": "作者旧稿场景安排",
            "pov": "作者旧稿视角", "hook": "作者旧稿出口", "recap": "作者旧稿回顾",
            "next_brief": "作者旧稿下一章任务", "foreshadowing_op": "作者旧稿伏笔", "status": "完成",
        })
        self.assertEqual(status, 200, edited)
        before = self.state()
        self.manuscript = self.manuscript_snapshot()
        self.source_blueprint = self.blueprint()
        second = self.finish(self.workflow(premise="第二轮全新故事：灯塔中的无名来客"))
        second_plan_ids = {
            chapter["id"] for volume in second["final_blueprint"]["volumes"]
            for chapter in volume["chapters"]
        }
        self.assertTrue(first_plan_ids.isdisjoint(second_plan_ids))
        status, applied = self.apply(second)
        self.assertEqual(status, 200, applied)
        status, rebuilt = self.request(
            "POST", "/api/blueprint/materialize", {"expected_version": applied["version"]},
        )
        self.assertEqual(status, 200, rebuilt)
        after = rebuilt["state"]
        self.assertEqual(len(after["chapters"]), len(before["chapters"]) + len(second_plan_ids))
        self.assertEqual(
            {chapter["plan_id"] for chapter in after["chapters"][:len(second_plan_ids)]},
            second_plan_ids,
        )
        for previous in before["chapters"]:
            self.assertEqual(next(chapter for chapter in after["chapters"]
                                  if chapter["id"] == previous["id"]), previous)
        chapter_ids = [chapter["id"] for chapter in after["chapters"]]
        status, repeated = self.request(
            "POST", "/api/blueprint/materialize", {"expected_version": rebuilt["version"]},
        )
        self.assertEqual(status, 200, repeated)
        self.assertEqual([chapter["id"] for chapter in repeated["state"]["chapters"]], chapter_ids)
        for previous in before["chapters"]:
            self.assertEqual(next(chapter for chapter in repeated["state"]["chapters"]
                                  if chapter["id"] == previous["id"]), previous)


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PlanningHTTPTests))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
