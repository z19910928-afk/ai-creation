#!/usr/bin/env python3
"""Offline SQLite fixtures for durable, staged story planning."""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_planning as planning  # noqa: E402
import moyu_workspace as workspace  # noqa: E402


def planning_ids(blueprint):
    return {
        item["id"]
        for volume in blueprint["volumes"]
        for item in (
            volume,
            *volume["chapters"],
            *(scene for chapter in volume["chapters"] for scene in chapter["scenes"]),
        )
    }


class PlanningWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="moyu-planning-")
        self.path = Path(self.temporary.name) / "planning.sqlite3"
        self.conn = self.connect()
        self.conn.executescript("""
            CREATE TABLE project(id TEXT PRIMARY KEY);
            CREATE TABLE branch(id TEXT PRIMARY KEY,project_id TEXT NOT NULL);
            CREATE TABLE app_blueprint(
                project_id TEXT,branch_id TEXT,version INTEGER NOT NULL,payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,PRIMARY KEY(project_id,branch_id));
            CREATE TABLE fixture_job(
                id TEXT PRIMARY KEY,project_id TEXT,branch_id TEXT,status TEXT,payload_json TEXT,result_json TEXT,
                billing_state TEXT);
            CREATE TABLE fixture_quota(job_id TEXT PRIMARY KEY);
            CREATE TABLE fixture_manuscript(id TEXT PRIMARY KEY,body TEXT);
            INSERT INTO project VALUES('book');
            INSERT INTO project VALUES('other');
            INSERT INTO branch VALUES('main','book');
            INSERT INTO branch VALUES('other-main','other');
            INSERT INTO fixture_manuscript VALUES('chapter','作者已有的正文，不得覆盖。');
        """)
        self.source = workspace.validate_blueprint({"premise": "作者已有的独立蓝图"})
        self.conn.execute(
            "INSERT INTO app_blueprint VALUES(?,?,?,?,?)",
            ("book", "main", 7, json.dumps(self.source, ensure_ascii=False), ""),
        )
        planning.install_schema(self.conn)
        self.conn.commit()
        self.initial = {"premise": "雨夜码头的第二把锁", "genre": "悬疑", "audience": "成年读者",
                        "tone": "克制", "contract": "有限视角与行动代价"}
        self.callbacks = {"create_job": self.create_job, "get_job": self.get_job,
                          "cancel_job": self.cancel_job}

    def tearDown(self) -> None:
        self.conn.close()
        self.temporary.cleanup()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def create_job(self, *, conn, payload, **kwargs):
        job_id = "fixture_" + payload["attempt_id"]
        conn.execute(
            "INSERT INTO fixture_job VALUES(?,?,?,?,?,?,?)",
            (job_id, payload["project_id"], payload["branch_id"], "queued",
             json.dumps(payload, ensure_ascii=False), None, "not_dispatched"),
        )
        conn.execute("INSERT INTO fixture_quota VALUES(?)", (job_id,))
        return {"id": job_id, "status": "queued"}

    def get_job(self, *, conn, job_id, **kwargs):
        row = conn.execute("SELECT * FROM fixture_job WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return {"id": job_id, "status": "unknown", "billing_state": "unknown"}
        return {"id": job_id, "status": row["status"], "project_id": row["project_id"],
                "branch_id": row["branch_id"], "billing_state": row["billing_state"],
                "result": json.loads(row["result_json"]) if row["result_json"] else None}

    def cancel_job(self, *, conn, job_id, **kwargs):
        row = conn.execute("SELECT status,billing_state FROM fixture_job WHERE id=?", (job_id,)).fetchone()
        status = "canceled" if row["status"] == "queued" else "unknown"
        conn.execute("UPDATE fixture_job SET status=? WHERE id=?", (status, job_id))
        return {"id": job_id, "status": status, "billing_state": row["billing_state"]}

    def workflow(self):
        return planning.create_workflow(self.conn, "book", "main", self.initial)

    def start(self, workflow):
        return planning.start_stage(
            self.conn, workflow["id"], self.callbacks, expected_stage=workflow["stage"], max_tokens=1600
        )

    def candidate(self, workflow):
        stage, blueprint = workflow["stage"], workflow["blueprint"]
        if stage == "overview":
            return {key: blueprint[key] for key in ("premise", "genre", "audience", "tone", "contract")}
        if stage == "volumes":
            return {"volumes": [{"id": volume["id"], "title": f"卷{index}：选择",
                                 "summary": f"第{index}卷推进选择与代价"}
                                for index, volume in enumerate(blueprint["volumes"], 1)]}
        if stage == "chapters":
            return {"volumes": [
                {"id": volume["id"], "chapters": [
                    {"id": chapter["id"], "title": f"章{index}：旧锁", "goal": "打开第二把锁",
                     "pov": "林岚", "hook": "门后响起拖链声"}
                    for index, chapter in enumerate(volume["chapters"], 1)]}
                for volume in blueprint["volumes"]]}
        return {"volumes": [
            {"id": volume["id"], "chapters": [
                {"id": chapter["id"], "scenes": [
                    {"id": scene["id"], "title": f"场景{index}", "goal": "取出月牙钥匙",
                     "location": "码头仓库", "pov": "林岚"}
                    for index, scene in enumerate(chapter["scenes"], 1)]}
                for chapter in volume["chapters"]]}
            for volume in blueprint["volumes"]]}

    def complete(self, workflow, candidate=None, **result_overrides):
        result = {"structured": candidate if candidate is not None else self.candidate(workflow),
                  "safe_to_apply": True, "completion_state": "complete", "finish_reason": "stop",
                  "billing_state": "usage_reported", **result_overrides}
        self.conn.execute(
            "UPDATE fixture_job SET status='complete',result_json=?,billing_state=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), result["billing_state"], workflow["attempt"]["job_id"]),
        )
        return planning.refresh_stage(self.conn, workflow["id"], self.callbacks)

    def confirm(self, workflow, **overrides):
        options = {"expected_stage": workflow["stage"], "attempt_id": workflow["attempt"]["id"],
                   "candidate_hash": workflow["attempt"]["candidate_hash"],
                   "expected_version": workflow["version"], **overrides}
        return planning.confirm_stage(self.conn, workflow["id"], self.callbacks, **options)

    def finish_workflow(self, workflow):
        for expected in planning.STAGES:
            self.assertEqual(workflow["stage"], expected)
            workflow = self.confirm(self.complete(self.start(workflow)))
        return workflow

    def test_schema_matches_four_stages_and_is_a_fresh_copy(self):
        schemas = planning.schema(self.conn)
        self.assertEqual(set(schemas), set(planning.STAGES))
        schemas["overview"]["required"].clear()
        self.assertTrue(planning.schema()["overview"]["required"])
        self.workflow()
        event = self.conn.execute("SELECT id FROM app_planning_event").fetchone()
        self.assertIsInstance(event["id"], str)
        self.assertTrue(event["id"].startswith("planevt_"))

    def test_creation_defaults_and_unique_workflow_ids(self):
        first, second = self.workflow(), self.workflow()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["source_version"], 7)
        self.assertEqual(first["stage"], "overview")
        self.assertEqual(first["initial"]["counts"], planning.DEFAULT_COUNTS)
        self.assertEqual(len(first["blueprint"]["volumes"]), 2)
        self.assertEqual(len(first["blueprint"]["volumes"][0]["chapters"]), 2)
        self.assertEqual(len(first["blueprint"]["volumes"][0]["chapters"][0]["scenes"]), 2)
        self.assertIsNone(first["final_blueprint"])

    def test_new_workflows_have_disjoint_volume_chapter_and_scene_identities(self):
        first, second = self.workflow(), self.workflow()
        first_ids, second_ids = planning_ids(first["blueprint"]), planning_ids(second["blueprint"])
        self.assertEqual(len(first_ids), 14)
        self.assertEqual(len(second_ids), 14)
        self.assertTrue(first_ids.isdisjoint(second_ids))
        for workflow, identities in ((first, first_ids), (second, second_ids)):
            namespace = workflow["id"].removeprefix("planwf_")
            self.assertTrue(all(namespace in identity for identity in identities))
            for identity in identities:
                self.assertEqual(workspace.validate_id(identity), identity)
            self.assertEqual(
                planning_ids(planning.get_workflow(self.conn, workflow["id"])["blueprint"]), identities,
            )

    def test_workflow_identities_stay_stable_across_retry_confirmation_and_restart(self):
        workflow = self.workflow()
        identities = planning_ids(workflow["blueprint"])
        started = self.start(workflow)
        self.conn.execute(
            "UPDATE fixture_job SET status='failed',billing_state='not_dispatched' WHERE id=?",
            (started["attempt"]["job_id"],),
        )
        failed = planning.refresh_stage(self.conn, workflow["id"], self.callbacks)
        self.assertEqual(failed["attempt"]["status"], "failed")
        self.assertEqual(planning_ids(failed["blueprint"]), identities)
        retry = self.start(failed)
        self.assertEqual(retry["attempt"]["attempt_no"], 2)
        confirmed = self.confirm(self.complete(retry))
        self.assertEqual(planning_ids(confirmed["blueprint"]), identities)
        self.conn.commit()
        self.conn.close()
        self.conn = self.connect()
        recovered = planning.get_workflow(self.conn, workflow["id"])
        self.assertEqual(planning_ids(recovered["blueprint"]), identities)
        refreshed = planning.refresh_stage(self.conn, workflow["id"], self.callbacks)
        self.assertEqual(planning_ids(refreshed["blueprint"]), identities)

    def test_cross_project_scope_and_branch_ownership_are_rejected(self):
        workflow = self.workflow()
        with self.assertRaises(planning.PlanningNotFound):
            planning.get_workflow(self.conn, workflow["id"], project_id="other")
        with self.assertRaises(planning.PlanningNotFound):
            planning.create_workflow(self.conn, "book", "other-main", self.initial)
        with workspace.use_scope("other", "other-main", "other-author"):
            with self.assertRaises(planning.PlanningNotFound):
                self.start(workflow)

    def test_start_has_one_local_job_and_unchanged_source_snapshot(self):
        workflow = self.start(self.workflow())
        self.assertEqual(workflow["attempt"]["status"], "queued")
        payload = json.loads(self.conn.execute("SELECT payload_json FROM fixture_job").fetchone()[0])
        self.assertEqual(payload["task"], "outline")
        self.assertEqual(payload["response_format"], "json")
        self.assertEqual(payload["context"]["_task_schema"], planning.schema()["overview"])
        self.assertEqual(payload["context"]["blueprint"]["premise"], self.initial["premise"])
        self.assertEqual(payload["source_version"], 7)
        with self.assertRaises(planning.PlanningConflict):
            self.start(workflow)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_job").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_quota").fetchone()[0], 1)

    def test_confirm_requires_current_complete_attempt_and_matching_hash(self):
        workflow = self.start(self.workflow())
        with self.assertRaises(planning.PlanningConflict):
            self.confirm(workflow)
        workflow = self.complete(workflow)
        with self.assertRaises(planning.PlanningError):
            planning.confirm_stage(self.conn, workflow["id"], self.callbacks)
        with self.assertRaises(planning.PlanningConflict):
            self.confirm(workflow, candidate_hash="bad-hash")
        with self.assertRaises(planning.PlanningConflict):
            self.confirm(workflow, attempt_id="older-attempt")
        with self.assertRaises(planning.PlanningConflict):
            self.confirm(workflow, expected_stage="scenes")
        confirmed = self.confirm(workflow)
        self.assertEqual(confirmed["stage"], "volumes")
        self.assertEqual(confirmed["attempts"][0]["status"], "confirmed")

    def test_all_four_confirmed_stages_build_valid_blueprint_without_writing_application_data(self):
        workflow = self.finish_workflow(self.workflow())
        self.assertEqual(workflow["status"], "completed")
        self.assertEqual(workflow["stage_index"], 4)
        self.assertEqual(workflow["final_blueprint"], workspace.validate_blueprint(workflow["final_blueprint"]))
        self.assertEqual(len(workflow["final_blueprint"]["volumes"]), 2)
        self.assertEqual(self.conn.execute("SELECT body FROM fixture_manuscript").fetchone()[0],
                         "作者已有的正文，不得覆盖。")
        row = self.conn.execute("SELECT version,payload_json FROM app_blueprint").fetchone()
        self.assertEqual(row["version"], 7)
        self.assertEqual(json.loads(row["payload_json"]), self.source)
        with self.assertRaises(planning.PlanningConflict):
            self.start(workflow)

    def test_each_stage_receives_frozen_previously_confirmed_content(self):
        workflow = self.start(self.workflow())
        candidate = self.candidate(workflow)
        candidate["premise"] = "已确认：钥匙属于守灯人"
        workflow = self.confirm(self.complete(workflow, candidate))
        workflow = self.start(workflow)
        payload = json.loads(self.conn.execute(
            "SELECT payload_json FROM fixture_job WHERE id=?", (workflow["attempt"]["job_id"],)
        ).fetchone()[0])
        self.assertEqual(payload["context"]["blueprint"]["premise"], candidate["premise"])
        self.assertEqual(payload["input"]["volumes"][0]["id"],
                         workflow["blueprint"]["volumes"][0]["id"])

    def test_source_blueprint_version_or_content_change_blocks_new_stage_and_confirmation(self):
        workflow = self.complete(self.start(self.workflow()))
        self.conn.execute("UPDATE app_blueprint SET version=8")
        with self.assertRaises(planning.PlanningConflict):
            self.confirm(workflow)
        self.conn.execute("UPDATE app_blueprint SET version=7,payload_json='{}'")
        with self.assertRaises(planning.PlanningConflict):
            self.confirm(workflow)
        fresh = planning.get_workflow(self.conn, workflow["id"])
        self.assertEqual(fresh["stage"], "overview")
        self.assertEqual(fresh["attempt"]["status"], "complete")

    def test_failed_attempt_can_be_manually_retried_and_unknown_cannot(self):
        workflow = self.start(self.workflow())
        self.conn.execute("UPDATE fixture_job SET status='failed',billing_state='not_dispatched'")
        workflow = planning.refresh(self.conn, workflow["id"], self.callbacks)
        self.assertEqual(workflow["attempt"]["status"], "failed")
        retried = self.start(workflow)
        self.assertEqual(retried["attempt"]["attempt_no"], 2)
        self.assertNotEqual(retried["attempt"]["id"], workflow["attempt"]["id"])
        self.conn.execute(
            "UPDATE fixture_job SET status='failed',billing_state='unknown' WHERE id=?",
            (retried["attempt"]["job_id"],),
        )
        workflow = planning.refresh(self.conn, retried["id"], self.callbacks)
        self.assertEqual(workflow["attempt"]["status"], "unknown")
        with self.assertRaises(planning.PlanningConflict):
            self.start(workflow)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_job").fetchone()[0], 2)

    def test_queued_cancellation_without_dispatch_can_be_manually_retried(self):
        workflow = self.start(self.workflow())
        self.conn.execute("UPDATE fixture_job SET status='canceled',billing_state='not_dispatched'")
        workflow = planning.refresh(self.conn, workflow["id"], self.callbacks)
        self.assertEqual(workflow["attempt"]["status"], "canceled")
        retried = self.start(workflow)
        self.assertEqual(retried["attempt"]["attempt_no"], 2)

    def test_unknown_can_recover_only_by_polling_the_same_durable_job(self):
        workflow = self.start(self.workflow())
        self.conn.execute("UPDATE fixture_job SET status='unknown',billing_state='unknown'")
        workflow = planning.refresh(self.conn, workflow["id"], self.callbacks)
        self.assertEqual(workflow["attempt"]["status"], "unknown")
        workflow = self.complete(workflow)
        self.assertEqual(workflow["attempt"]["status"], "complete")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_job").fetchone()[0], 1)

    def test_protocol_incomplete_and_invalid_structure_cannot_be_confirmed(self):
        workflow = self.start(self.workflow())
        workflow = self.complete(workflow, safe_to_apply=False, completion_state="incomplete",
                                 finish_reason="length")
        self.assertEqual(workflow["attempt"]["status"], "failed")
        with self.assertRaises(planning.PlanningConflict):
            self.confirm(workflow)
        workflow = self.start(workflow)
        invalid = self.candidate(workflow)
        invalid["unexpected"] = "not allowed"
        workflow = self.complete(workflow, invalid)
        self.assertEqual(workflow["attempt"]["status"], "failed")

    def test_missing_foreign_or_changed_ids_are_rejected_before_confirmation(self):
        workflow = self.confirm(self.complete(self.start(self.workflow())))
        workflow = self.start(workflow)
        invalid = self.candidate(workflow)
        invalid["volumes"][0]["id"] = "different-volume"
        workflow = self.complete(workflow, invalid)
        self.assertEqual(workflow["attempt"]["status"], "failed")
        self.assertEqual(planning.get_workflow(self.conn, workflow["id"])["stage"], "volumes")

    def test_saved_candidate_tampering_is_detected(self):
        workflow = self.complete(self.start(self.workflow()))
        candidate = copy.deepcopy(workflow["attempt"]["candidate"])
        candidate["premise"] += "外部篡改"
        self.conn.execute("UPDATE app_planning_attempt SET candidate_json=? WHERE id=?",
                          (json.dumps(candidate, ensure_ascii=False), workflow["attempt"]["id"]))
        with self.assertRaises(planning.PlanningConflict):
            self.confirm(workflow)

    def test_restart_preserves_attempt_and_refresh_is_idempotent(self):
        workflow = self.start(self.workflow())
        self.conn.commit()
        self.conn.close()
        self.conn = self.connect()
        recovered = planning.get_workflow(self.conn, workflow["id"])
        self.assertEqual(recovered["attempt"]["job_id"], workflow["attempt"]["job_id"])
        workflow = self.complete(recovered)
        count = self.conn.execute("SELECT COUNT(*) FROM app_planning_event").fetchone()[0]
        fresh = planning.refresh(self.conn, workflow["id"], self.callbacks)
        self.assertEqual(fresh["attempt"]["candidate_hash"], workflow["attempt"]["candidate_hash"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_planning_event").fetchone()[0], count)

    def test_unchanged_active_job_polls_do_not_rewrite_attempt_or_append_events(self):
        for status in ("queued", "running", "unknown"):
            with self.subTest(status=status):
                workflow = self.start(self.workflow())
                self.conn.execute(
                    "UPDATE fixture_job SET status=?,billing_state=? WHERE id=?",
                    (status, "unknown" if status == "unknown" else "not_dispatched",
                     workflow["attempt"]["job_id"]),
                )
                workflow = planning.refresh_stage(self.conn, workflow["id"], self.callbacks)
                self.assertEqual(workflow["attempt"]["status"], status)
                self.conn.execute(
                    "UPDATE app_planning_attempt SET updated_at=? WHERE id=?",
                    ("2026-01-01T00:00:00+00:00", workflow["attempt"]["id"]),
                )
                snapshot = tuple(self.conn.execute(
                    "SELECT * FROM app_planning_attempt WHERE id=?",
                    (workflow["attempt"]["id"],),
                ).fetchone())
                events = [
                    tuple(row) for row in self.conn.execute(
                        "SELECT * FROM app_planning_event WHERE workflow_id=? ORDER BY rowid",
                        (workflow["id"],),
                    )
                ]
                external_events = []
                callbacks = {**self.callbacks, "append_event": lambda **event: external_events.append(event)}
                for _ in range(4):
                    fresh = planning.refresh_stage(self.conn, workflow["id"], callbacks)
                    self.assertEqual(fresh["attempt"]["status"], status)
                self.assertEqual(tuple(self.conn.execute(
                    "SELECT * FROM app_planning_attempt WHERE id=?",
                    (workflow["attempt"]["id"],),
                ).fetchone()), snapshot)
                self.assertEqual([
                    tuple(row) for row in self.conn.execute(
                        "SELECT * FROM app_planning_event WHERE workflow_id=? ORDER BY rowid",
                        (workflow["id"],),
                    )
                ], events)
                self.assertEqual(external_events, [])

    def test_changed_unknown_error_is_recorded_once_and_does_not_allow_retry(self):
        workflow = self.start(self.workflow())
        self.conn.execute(
            "UPDATE fixture_job SET status='failed',billing_state='unknown' WHERE id=?",
            (workflow["attempt"]["job_id"],),
        )
        workflow = planning.refresh_stage(self.conn, workflow["id"], self.callbacks)
        self.assertEqual(workflow["attempt"]["status"], "unknown")
        events_before = self.conn.execute(
            "SELECT COUNT(*) FROM app_planning_event WHERE workflow_id=?", (workflow["id"],),
        ).fetchone()[0]
        callbacks = {
            **self.callbacks,
            "get_job": lambda **kwargs: {
                **self.get_job(**kwargs), "error": "外部任务记录暂时不可查询",
            },
        }
        for _ in range(4):
            fresh = planning.refresh_stage(self.conn, workflow["id"], callbacks)
            self.assertEqual(fresh["attempt"]["status"], "unknown")
            self.assertEqual(fresh["attempt"]["error"], "外部任务记录暂时不可查询")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM app_planning_event WHERE workflow_id=?", (workflow["id"],),
        ).fetchone()[0], events_before + 1)
        event = self.conn.execute(
            "SELECT event_type,payload_json FROM app_planning_event WHERE workflow_id=? ORDER BY rowid DESC LIMIT 1",
            (workflow["id"],),
        ).fetchone()
        self.assertEqual(event["event_type"], "stage.unknown")
        self.assertEqual(json.loads(event["payload_json"])["error"], "外部任务记录暂时不可查询")
        with self.assertRaises(planning.PlanningConflict):
            self.start(fresh)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_job").fetchone()[0], 1)

    def test_create_callback_failure_rolls_back_attempt_and_quota(self):
        workflow = self.workflow()
        self.conn.commit()
        def failing(**kwargs):
            self.create_job(**kwargs)
            raise RuntimeError("local queue insert failed")
        with self.assertRaisesRegex(RuntimeError, "local queue"):
            planning.start_stage(self.conn, workflow["id"], {**self.callbacks, "create_job": failing})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_planning_attempt").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_job").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_quota").fetchone()[0], 0)
        self.assertTrue(self.conn.in_transaction)

    def test_confirmation_event_failure_rolls_back_attempt_and_stage(self):
        workflow = self.complete(self.start(self.workflow()))
        self.conn.commit()
        def failing_event(**kwargs):
            if kwargs["event_type"] == "stage.confirmed":
                raise RuntimeError("event persistence failed")
        callbacks = {**self.callbacks, "append_event": failing_event}
        with self.assertRaisesRegex(RuntimeError, "event persistence"):
            planning.confirm_stage(
                self.conn, workflow["id"], callbacks, expected_stage=workflow["stage"],
                attempt_id=workflow["attempt"]["id"], candidate_hash=workflow["attempt"]["candidate_hash"]
            )
        state = planning.get_workflow(self.conn, workflow["id"])
        self.assertEqual(state["stage"], "overview")
        self.assertEqual(state["attempt"]["status"], "complete")
        self.assertEqual(state["version"], 0)

    def test_caller_rollback_owns_the_full_workflow_and_queue_transaction(self):
        workflow = self.start(self.workflow())
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_planning_workflow").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_job").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_quota").fetchone()[0], 0)
        with self.assertRaises(planning.PlanningNotFound):
            planning.get_workflow(self.conn, workflow["id"])

    def test_cancel_queued_and_running_attempts_preserves_unknown_cost(self):
        workflow = self.start(self.workflow())
        canceled = planning.cancel_workflow(self.conn, workflow["id"], self.callbacks)
        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(canceled["attempt"]["status"], "canceled")
        self.assertEqual(planning.cancel_workflow(self.conn, workflow["id"], self.callbacks)["status"], "canceled")
        other = self.start(self.workflow())
        self.conn.execute("UPDATE fixture_job SET status='running',billing_state='unknown' WHERE id=?",
                          (other["attempt"]["job_id"],))
        other = planning.refresh(self.conn, other["id"], self.callbacks)
        canceled = planning.cancel_workflow(self.conn, other["id"], self.callbacks)
        self.assertEqual(canceled["attempt"]["status"], "unknown")
        self.assertEqual(canceled["status"], "canceled")
        with self.assertRaises(planning.PlanningConflict):
            self.start(canceled)

    def test_concurrent_start_creates_only_one_durable_job(self):
        workflow = self.workflow()
        self.conn.commit()
        entered = threading.Event()
        release = threading.Event()
        outcomes = []
        errors = []
        def delayed_create(**kwargs):
            entered.set()
            release.wait(timeout=3)
            return self.create_job(**kwargs)
        def run_first():
            conn = self.connect()
            try:
                result = planning.start_stage(conn, workflow["id"], {
                    **self.callbacks, "create_job": delayed_create,
                })
                conn.commit()
                outcomes.append(result)
            except Exception as exc:
                errors.append(exc)
            finally:
                conn.close()
        def run_second():
            conn = self.connect()
            try:
                result = planning.start_stage(conn, workflow["id"], self.callbacks)
                conn.commit()
                outcomes.append(result)
            except Exception as exc:
                errors.append(exc)
                conn.rollback()
            finally:
                conn.close()
        first = threading.Thread(target=run_first)
        second = threading.Thread(target=run_second)
        first.start()
        self.assertTrue(entered.wait(timeout=3))
        second.start()
        time.sleep(0.03)
        release.set()
        first.join(timeout=5)
        second.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], planning.PlanningConflict)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_job").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fixture_quota").fetchone()[0], 1)

    def test_schema_install_preserves_open_transaction_and_plain_tuple_connections(self):
        self.conn.execute("UPDATE fixture_manuscript SET body='本次未提交修改'")
        planning.install_schema(self.conn)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT body FROM fixture_manuscript").fetchone()[0],
                         "作者已有的正文，不得覆盖。")
        plain = sqlite3.connect(":memory:")
        try:
            planning.install_schema(plain)
            created = planning.create_workflow(plain, "plain-book", "plain-main", self.initial)
            self.assertEqual(planning.get_workflow(plain, created["id"])["stage"], "overview")
        finally:
            plain.close()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PlanningWorkflowTests)
    )
    print(json.dumps({"passed": result.wasSuccessful()}, ensure_ascii=False))
    raise SystemExit(0 if result.wasSuccessful() else 1)
