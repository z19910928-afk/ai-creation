#!/usr/bin/env python3
"""Project archive coverage for durable staged planning records."""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import moyu_backups as backups  # noqa: E402
import moyu_planning as planning  # noqa: E402


class PlanningBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = self.make_connection()
        self.target = self.make_connection()
        self.install_common(self.source)
        self.install_common(self.target)
        self.populate(self.source)

    def tearDown(self) -> None:
        self.source.close()
        self.target.close()

    def make_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def install_common(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE project(id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE branch(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,status TEXT);
        """)
        planning.install_schema(conn)
        conn.execute("""
            CREATE TABLE app_planning_application(
              id TEXT PRIMARY KEY,
              workflow_id TEXT NOT NULL UNIQUE REFERENCES app_planning_workflow(id),
              project_id TEXT NOT NULL, branch_id TEXT NOT NULL,
              source_version INTEGER NOT NULL, source_hash TEXT NOT NULL,
              final_hash TEXT NOT NULL, result_version INTEGER NOT NULL,
              applied_at TEXT NOT NULL
            )
        """)
        conn.commit()

    def populate(self, conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO project VALUES(?,?)", ("book", "原作品"))
        conn.execute("INSERT INTO project VALUES(?,?)", ("other", "其他作品"))
        conn.execute("INSERT INTO branch VALUES(?,?,?)", ("main", "book", "active"))
        conn.execute("INSERT INTO branch VALUES(?,?,?)", ("other-main", "other", "active"))
        initial = {
            "premise": "雨夜码头的第二把锁", "genre": "悬疑", "audience": "成年读者",
            "tone": "克制", "contract": "有限视角",
            "counts": {"volumes": 1, "chapters_per_volume": 1, "scenes_per_chapter": 2},
        }
        blueprint = {
            "premise": initial["premise"], "genre": initial["genre"],
            "audience": initial["audience"], "tone": initial["tone"],
            "contract": initial["contract"], "counts": initial["counts"],
            "volumes": [{
                "id": "vol_001", "title": "第一卷", "summary": "锁与潮汐",
                "chapters": [{
                    "id": "plan_001_001", "title": "仓库", "goal": "找到锁",
                    "pov": "林岚", "hook": "门后响起拖链声", "scenes": [{
                        "id": "scene_001_001_001", "title": "雨夜", "goal": "进入仓库",
                        "location": "旧码头", "pov": "林岚",
                    }, {
                        "id": "scene_001_001_002", "title": "第二把锁", "goal": "取出钥匙",
                        "location": "仓库", "pov": "林岚",
                    }],
                }],
            }],
        }
        now = "2026-10-13T00:00:00+00:00"
        source_hash = planning._hash({"version": 11, "blueprint": blueprint})
        conn.execute(
            "INSERT INTO app_planning_workflow VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "planwf_book", "book", "main", "active", "overview", 0, 3,
                json.dumps(initial, ensure_ascii=False),
                json.dumps(blueprint, ensure_ascii=False),
                json.dumps(blueprint, ensure_ascii=False),
                None, 11, source_hash, None, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO app_planning_workflow VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "planwf_pending", "book", "main", "active", "overview", 0, 1,
                json.dumps(initial, ensure_ascii=False),
                json.dumps(blueprint, ensure_ascii=False),
                json.dumps(blueprint, ensure_ascii=False),
                None, 11, source_hash, None, now, now,
            ),
        )
        # Unknown external execution and a complete unconfirmed candidate
        # belong to separate workflows, as they do in the live application.
        conn.execute(
            "INSERT INTO app_planning_attempt VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "planatt_unknown", "planwf_pending", "overview", 1, "unknown",
                "job_unknown", 11, source_hash, None, None, 1800,
                "外部请求状态未知", now, now,
            ),
        )
        candidate = {
            "premise": "已确认的故事核心", "genre": "悬疑",
            "audience": "成年读者", "tone": "克制", "contract": "有限视角",
        }
        candidate_hash = planning._hash(candidate)
        conn.execute(
            "INSERT INTO app_planning_attempt VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "planatt_complete", "planwf_book", "overview", 1, "complete",
                "job_complete", 11, source_hash, json.dumps(candidate, ensure_ascii=False),
                candidate_hash, 1800, None, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO app_planning_event VALUES(?,?,?,?,?,?)",
            ("planevt_book", "planwf_pending", "planatt_unknown", "stage.dispatch_unknown",
             json.dumps({"candidate_hash": None}, ensure_ascii=False), now),
        )
        final_blueprint = copy.deepcopy(blueprint)
        final_blueprint["premise"] = candidate["premise"]
        conn.execute(
            "INSERT INTO app_planning_workflow VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "planwf_applied", "book", "main", "completed", "scenes", 4, 4,
                json.dumps(initial, ensure_ascii=False),
                json.dumps(final_blueprint, ensure_ascii=False),
                json.dumps(blueprint, ensure_ascii=False),
                json.dumps(final_blueprint, ensure_ascii=False),
                11, source_hash, None, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO app_planning_application VALUES(?,?,?,?,?,?,?,?,?)",
            ("planapply_book", "planwf_applied", "book", "main", 11,
             source_hash, planning._hash(final_blueprint), 12, now),
        )
        # Same table names in another book prove capture does not cross scope.
        other_blueprint = copy.deepcopy(blueprint)
        conn.execute(
            "INSERT INTO app_planning_workflow VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "planwf_other", "other", "other-main", "active", "overview", 0, 0,
                json.dumps(initial, ensure_ascii=False),
                json.dumps(other_blueprint, ensure_ascii=False),
                json.dumps(other_blueprint, ensure_ascii=False),
                None, 2, planning._hash({"version": 2, "blueprint": other_blueprint}),
                None, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO app_planning_attempt VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "planatt_other", "planwf_other", "overview", 1, "queued",
                "job_other", 2, planning._hash({"version": 2, "blueprint": other_blueprint}),
                None, None, 1000, None, now, now,
            ),
        )
        conn.commit()

    def test_capture_includes_workflow_children_and_excludes_foreign_book(self) -> None:
        payload = backups.capture(self.source, "book")
        tables = payload["tables"]
        self.assertEqual(
            {row["id"] for row in tables["app_planning_workflow"]},
            {"planwf_book", "planwf_pending", "planwf_applied"},
        )
        self.assertEqual(
            {row["id"] for row in tables["app_planning_attempt"]},
            {"planatt_unknown", "planatt_complete"},
        )
        self.assertEqual([row["id"] for row in tables["app_planning_event"]], ["planevt_book"])
        self.assertEqual([row["id"] for row in tables["app_planning_application"]], ["planapply_book"])
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("planwf_other", encoded)
        self.assertNotIn("planatt_other", encoded)

    def test_capture_rows_are_stable_and_candidate_hashes_are_preserved(self) -> None:
        first = backups.capture(self.source, "book")
        second = backups.capture(self.source, "book")
        self.assertEqual(first, second)
        row = next(item for item in first["tables"]["app_planning_attempt"]
                   if item["id"] == "planatt_complete")
        self.assertEqual(row["candidate_hash"], planning._hash(json.loads(row["candidate_json"])))
        self.assertEqual(json.loads(row["candidate_json"]), {
            "audience": "成年读者", "contract": "有限视角", "genre": "悬疑",
            "premise": "已确认的故事核心", "tone": "克制",
        })
        self.assertEqual(row["candidate_json"], self.source.execute(
            "SELECT candidate_json FROM app_planning_attempt WHERE id='planatt_complete'"
        ).fetchone()[0])

    def test_transplant_maps_relational_ids_but_keeps_historical_planning_json(self) -> None:
        payload = backups.capture(self.source, "book")
        result = backups.transplant(self.target, payload, "copy")
        self.assertEqual(result["project_id"], "copy")
        self.assertNotEqual(result["identity_map"]["planwf_book"], "planwf_book")
        new_workflow = self.target.execute(
            "SELECT * FROM app_planning_workflow WHERE id=?",
            (result["identity_map"]["planwf_book"],),
        ).fetchone()
        source_workflow = self.source.execute(
            "SELECT * FROM app_planning_workflow WHERE id='planwf_book'"
        ).fetchone()
        self.assertIsNotNone(new_workflow)
        self.assertEqual(new_workflow["status"], "canceled")
        self.assertEqual(new_workflow["cancel_reason"], backups.PLANNING_RESTORE_REASON)
        self.assertEqual(new_workflow["source_version"], 11)
        self.assertEqual(new_workflow["source_hash"],
                         planning._hash({"version": 11, "blueprint": json.loads(new_workflow["source_blueprint_json"])}))
        for key in ("initial_json", "blueprint_json", "source_blueprint_json", "final_blueprint_json"):
            self.assertEqual(new_workflow[key], source_workflow[key])
        attempts = self.target.execute(
            "SELECT * FROM app_planning_attempt WHERE workflow_id=?",
            (new_workflow["id"],),
        ).fetchall()
        self.assertEqual({row["status"] for row in attempts}, {"complete"})
        event = self.target.execute(
            "SELECT * FROM app_planning_event WHERE workflow_id=?",
            (result["identity_map"]["planwf_pending"],),
        ).fetchone()
        self.assertIsNotNone(event)
        self.assertTrue(event["id"].startswith("app_planning_event_"))
        self.assertEqual(event["attempt_id"], result["identity_map"]["planatt_unknown"])
        application = self.target.execute(
            "SELECT * FROM app_planning_application"
        ).fetchone()
        source_application = self.source.execute(
            "SELECT * FROM app_planning_application"
        ).fetchone()
        self.assertEqual(application["project_id"], "copy")
        self.assertEqual(application["workflow_id"], result["identity_map"]["planwf_applied"])
        self.assertEqual(application["branch_id"], result["identity_map"]["main"])
        for key in ("source_version", "source_hash", "final_hash", "result_version", "applied_at"):
            self.assertEqual(application[key], source_application[key])
        self.assertFalse(self.target.execute("PRAGMA foreign_key_check").fetchone())
        self.assertFalse(self.target.execute(
            "SELECT 1 FROM app_planning_workflow WHERE project_id='book'"
        ).fetchone())
        self.assertFalse(self.target.execute(
            "SELECT 1 FROM fixture_job"
        ).fetchone() if self.target.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fixture_job'"
        ).fetchone() else False)

    def test_transplant_preserves_completed_candidate_for_reading_but_does_not_resume_job(self) -> None:
        payload = backups.capture(self.source, "book")
        backups.transplant(self.target, payload, "copy")
        attempt = self.target.execute(
            "SELECT status,candidate_json,candidate_hash,job_id FROM app_planning_attempt "
            "WHERE status='complete'"
        ).fetchone()
        self.assertEqual(attempt["status"], "complete")
        self.assertEqual(attempt["candidate_hash"],
                         planning._hash(json.loads(attempt["candidate_json"])))
        self.assertEqual(attempt["job_id"], "job_complete")
        self.assertEqual(
            {row["status"] for row in self.target.execute("SELECT status FROM app_planning_workflow")},
            {"canceled"},
        )

    def test_unknown_external_attempt_never_becomes_queued_on_restore(self) -> None:
        payload = backups.capture(self.source, "book")
        backups.transplant(self.target, payload, "copy")
        statuses = {
            row["status"] for row in self.target.execute(
                "SELECT status FROM app_planning_attempt"
            )
        }
        self.assertEqual(statuses, {"complete", "unknown"})
        self.assertNotIn("queued", statuses)
        self.assertNotIn("running", statuses)

    def test_queued_and_running_attempts_restore_as_unknown(self) -> None:
        for original_status in ("queued", "running"):
            with self.subTest(original_status=original_status):
                self.source.execute(
                    "UPDATE app_planning_attempt SET status=? WHERE id='planatt_unknown'",
                    (original_status,),
                )
                result = backups.transplant(
                    self.target, backups.capture(self.source, "book"), "copy_" + original_status
                )
                row = self.target.execute(
                    "SELECT status,error FROM app_planning_attempt WHERE id=?",
                    (result["identity_map"]["planatt_unknown"],),
                ).fetchone()
                self.assertEqual(row["status"], "unknown")
                self.assertIn("未重复执行", row["error"])

    def test_restored_workflows_cannot_dispatch_or_confirm_and_refresh_is_read_only(self) -> None:
        payload = backups.capture(self.source, "book")
        result = backups.transplant(self.target, payload, "copy")
        self.target.commit()
        create_job, get_job = Mock(), Mock()
        callbacks = {"create_job": create_job, "get_job": get_job}
        for row in payload["tables"]["app_planning_workflow"]:
            workflow_id = result["identity_map"][row["id"]]
            with self.assertRaises(planning.PlanningConflict):
                planning.start_stage(self.target, workflow_id, callbacks)
            with self.assertRaises(planning.PlanningConflict):
                planning.confirm_stage(self.target, workflow_id, callbacks)
            restored = planning.refresh_stage(self.target, workflow_id, callbacks)
            self.assertEqual(restored["status"], "canceled")
        create_job.assert_not_called()
        get_job.assert_not_called()

    def test_completed_application_final_hash_matches_restored_frozen_blueprint(self) -> None:
        result = backups.transplant(self.target, backups.capture(self.source, "book"), "copy")
        workflow = self.target.execute(
            "SELECT final_blueprint_json,source_blueprint_json FROM app_planning_workflow WHERE id=?",
            (result["identity_map"]["planwf_applied"],),
        ).fetchone()
        application = self.target.execute("SELECT * FROM app_planning_application").fetchone()
        self.assertEqual(application["final_hash"],
                         planning._hash(json.loads(workflow["final_blueprint_json"])))
        self.assertEqual(application["source_hash"], planning._hash({
            "version": application["source_version"],
            "blueprint": json.loads(workflow["source_blueprint_json"]),
        }))

    def test_invalid_child_workflow_is_rejected_before_target_mutation(self) -> None:
        payload = backups.capture(self.source, "book")
        payload["tables"]["app_planning_attempt"][0]["workflow_id"] = "planwf_other"
        with self.assertRaisesRegex(ValueError, "不属于当前作品"):
            backups.transplant(self.target, payload, "copy")
        self.assertFalse(self.target.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_planning_workflow'"
        ).fetchone() is None)
        self.assertFalse(self.target.execute(
            "SELECT 1 FROM app_planning_workflow WHERE project_id='copy'"
        ).fetchone())


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PlanningBackupTests)
    )
    print(json.dumps({"passed": result.wasSuccessful()}, ensure_ascii=False))
    raise SystemExit(0 if result.wasSuccessful() else 1)
