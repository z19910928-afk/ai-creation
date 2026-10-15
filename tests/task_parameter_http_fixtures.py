#!/usr/bin/env python3
"""Task-specific settings through isolated SQLite, real HTTP and a free local provider."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import provider_queue_integration_fixtures as provider_fixture
import moyu_settings
import server

TASKS = (
    "continue", "polish", "outline", "scene", "summary", "check", "brainstorm",
    "chapter_analysis", "semantic_review",
)
SOURCES = (
    "server.py", "moyu_settings.py", "moyu_provider.py", "moyu_planning.py",
    "moyu_backups.py", "moyu_portable.py", "moyu_workspace.py",
    "docs/schema-v1.sql", "docs/schema-v1.1-migration.sql",
    "tests/provider_queue_integration_fixtures.py",
    "tests/task_parameter_http_fixtures.py",
)
SYNTHETIC_KEY = "synthetic-task-parameter-private-key"
NESTED_SECRET = "synthetic-task-parameter-nested-private-value"
REPORT = {"http": [], "observations": [], "local_provider_calls": 0}


def digest(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fingerprints():
    return {name: digest((ROOT / name).read_bytes()) for name in SOURCES}


def database_snapshot():
    with sqlite3.connect(server.DB_PATH) as conn:
        schema = list(conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        tables = {}
        for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            try:
                rows = list(conn.execute("SELECT rowid,* FROM " + quoted + " ORDER BY rowid"))
            except sqlite3.OperationalError as error:
                if "no such column: rowid" not in str(error):
                    raise
                rows = list(conn.execute("SELECT * FROM " + quoted))
            tables[name] = rows
        return {"schema": schema, "tables": tables}


def snapshot_digest(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             default=lambda item: {"bytes_sha256": digest(item)}))


class TaskParameterHTTPTests(unittest.TestCase):
    # Reuse the local harness without inheriting its unrelated test methods.
    setUpClass = classmethod(provider_fixture.IntegrationTests.setUpClass.__func__)
    tearDownClass = classmethod(provider_fixture.IntegrationTests.tearDownClass.__func__)
    settings = provider_fixture.IntegrationTests.settings
    wait_job = provider_fixture.IntegrationTests.wait_job

    def setUp(self):
        provider_fixture.IntegrationTests.setUp(self)
        self.calls_before = len(provider_fixture.Handler.calls)
        self.settings(key=SYNTHETIC_KEY, model="task-parameter-fixture")

    def tearDown(self):
        provider_fixture.IntegrationTests.tearDown(self)
        REPORT["local_provider_calls"] += len(provider_fixture.Handler.calls) - self.calls_before

    def request(self, method, route, payload=None):
        status, result = provider_fixture.IntegrationTests.request(self, method, route, payload)
        REPORT["http"].append({
            "test": self._testMethodName, "method": method, "path": route,
            "status": status, "error": result.get("error"),
        })
        return status, result

    def save(self, **data):
        status, result = self.request("PUT", "/api/ai/settings", data)
        self.assertEqual(status, 200, result)
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(result, ensure_ascii=False))
        return result["settings"]

    def preview(self, task="continue", **data):
        status, result = self.request("POST", "/api/ai/prompt-preview", {
            "task": task, "chapter_id": self.chapter_id, **data,
        })
        self.assertEqual(status, 200, result)
        return result["request"]

    def config(self):
        with server.connect() as conn:
            return moyu_settings.provider_settings(conn, self.project)

    def credentials_snapshot(self):
        directory = self.root / "private-keys"
        return {path.name: digest(path.read_bytes()) for path in directory.glob("*.json")}

    def job_audit(self, job):
        with server.connect() as conn:
            row = conn.execute("SELECT sampling_json,consent_json FROM ai_run WHERE id=?",
                               (job["ai_run_id"],)).fetchone()
            return {"sampling": json.loads(row["sampling_json"]),
                    "consent": json.loads(row["consent_json"])}

    def queue(self, task="continue", **data):
        status, job = self.request("POST", "/api/ai/queue", {
            "task": task, "chapter_id": self.chapter_id, "defer": True, **data,
        })
        self.assertEqual(status, 202, job)
        return job

    def process(self, job):
        status, result = self.request("POST", f"/api/ai/jobs/{job['id']}/process", {})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["status"], "success", result)
        return provider_fixture.Handler.calls[-1]

    def raw_zip(self, backup_id):
        self.assertTrue(hasattr(self, "api"))
        route = f"/api/backups/{backup_id}/download"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.api.server_port}" + route,
            headers={"X-Moyu-Project": self.project}, method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)
            package = response.read()
        REPORT["http"].append({
            "test": self._testMethodName, "method": "GET", "path": route,
            "status": 200, "bytes": len(package), "sha256": digest(package),
        })
        return package

    def restore(self, package):
        status, preview = self.request("POST", "/api/backups/import/preview", {
            "filename": "synthetic-task-parameters.zip",
            "data_base64": base64.b64encode(package).decode("ascii"),
        })
        self.assertEqual(status, 200, preview)
        status, restored = self.request("POST", "/api/backups/import/apply", {
            "preview_id": preview["preview_id"], "archive_sha256": preview["archive_sha256"],
            "title": "Synthetic task parameter restored copy", "confirm": True,
        })
        self.assertEqual(status, 201, restored)
        self.project, self.branch = restored["project_id"], restored["branch_id"]
        self.chapter_id = restored["state"]["chapters"][0]["id"]
        status, public = self.request("GET", "/api/ai/settings")
        self.assertEqual(status, 200, public)
        return public["settings"]

    def stored_zip_settings(self, package):
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            payload = json.loads(archive.read("project.json"))
            self.assertNotIn(SYNTHETIC_KEY, archive.read("project.json").decode("utf-8"))
        row = next(item for item in payload["tables"]["app_model_settings"]
                   if item["project_id"] == self.project)
        return json.loads(row["settings_json"])

    def planning_job(self, stage="overview", *, maximum=None):
        status, workflow = self.request("POST", "/api/planning-workflows", {
            "initial": {"premise": "Synthetic fixed planning premise",
                        "counts": {"volumes": 1, "chapters_per_volume": 1,
                                   "scenes_per_chapter": 1}},
        })
        self.assertEqual(status, 201, workflow)
        if stage != "overview":
            # Fixture setup establishes a later stage without paying for prior stages.
            with server.connect() as conn:
                conn.execute("UPDATE app_planning_workflow SET current_stage=?,stage_index=? "
                             "WHERE id=?", (stage, 3, workflow["id"]))
            status, workflow = self.request("GET", "/api/planning-workflows/" + workflow["id"])
            self.assertEqual(status, 200, workflow)
        data = {"expected_stage": stage, "expected_version": workflow["version"], "defer": True}
        if maximum is not None:
            data["max_tokens"] = maximum
        status, started = self.request("POST", f"/api/planning-workflows/{workflow['id']}/start", data)
        self.assertEqual(status, 202, started)
        with server.connect() as conn:
            row = conn.execute("SELECT * FROM app_ai_job WHERE id=?",
                               (started["attempt"]["job_id"],)).fetchone()
            return dict(row)

    def test_01_nine_task_settings_roundtrip_and_preview_use_distinct_parameters(self):
        overrides = {
            task: {"temperature": index / 10, "max_tokens": 1100 + index,
                   "seed": index, "top_p": 0.61 + index / 100}
            for index, task in enumerate(TASKS)
        }
        self.save(parameters={"temperature": 1.7, "top_p": 0.99, "max_tokens": 3000},
                  task_parameters=overrides)
        status, public = self.request("GET", "/api/ai/settings")
        self.assertEqual(status, 200, public)
        self.assertEqual(public["settings"]["task_parameters"], overrides)
        config = self.config()
        for task, parameters in overrides.items():
            with self.subTest(task=task):
                expected_sampling = {key: value for key, value in parameters.items()
                                     if key != "max_tokens"}
                self.assertEqual(config["task_parameters"][task], expected_sampling)
                self.assertEqual(config["task_max_tokens"][task], parameters["max_tokens"])
                actual = self.preview(task)
                for key, value in parameters.items():
                    self.assertEqual(actual[key], value)
        REPORT["observations"].append({"test": self._testMethodName, "tasks": len(TASKS),
                                       "preview_dispatches": len(provider_fixture.Handler.calls) - self.calls_before})
        self.assertEqual(len(provider_fixture.Handler.calls), self.calls_before)

    def test_02_zero_values_clear_overrides_and_omitted_field_preserves_defaults(self):
        self.save(task_parameters={"continue": {"temperature": 0, "seed": 0, "top_p": 0,
                                               "max_tokens": 1}})
        self.assertEqual(self.preview()["temperature"], 0)
        self.assertEqual(self.preview()["seed"], 0)
        self.assertEqual(self.preview()["top_p"], 0)
        self.assertEqual(self.preview()["max_tokens"], 1)
        unchanged = self.save(styles={"tone": "Synthetic style"})
        self.assertEqual(unchanged["task_parameters"]["continue"]["temperature"], 0)
        self.assertEqual(unchanged["task_parameters"]["continue"]["max_tokens"], 1)
        cleared_task = self.save(task_parameters={"continue": {}})
        self.assertEqual(cleared_task["task_parameters"], {})
        self.assertEqual(self.preview()["max_tokens"], 3000)
        self.save(task_parameters={"continue": {"temperature": 0.2}, "polish": {"max_tokens": 4200}})
        cleared_all = self.save(task_parameters={})
        self.assertEqual(cleared_all["task_parameters"], {})
        self.assertEqual(self.config()["task_parameters"], {})
        self.assertEqual(self.config()["task_max_tokens"], {})
        with server.connect() as conn:
            raw = json.loads(conn.execute("SELECT settings_json FROM app_model_settings "
                                          "WHERE project_id=?", (self.project,)).fetchone()[0])
            raw.pop("task_parameters", None)
            conn.execute("UPDATE app_model_settings SET settings_json=? WHERE project_id=?",
                         (server.json_text(raw), self.project))
        self.assertEqual(self.preview("chapter_analysis")["max_tokens"], 3000)
        self.assertEqual(self.config()["task_parameters"], {})

    def test_03_invalid_task_settings_are_atomic_and_do_not_store_new_credentials(self):
        self.save(task_parameters={"continue": {"temperature": 0, "max_tokens": 4100}})
        invalid = [
            None, [], "disabled", {"unknown": {"temperature": 0}},
            {"continue": None}, {"continue": []}, {"continue": "disabled"},
            {"continue": {"api_key": NESTED_SECRET}},
            {"continue": {"temperature": True}}, {"continue": {"temperature": -0.1}},
            {"continue": {"temperature": 2.1}}, {"continue": {"temperature": float("nan")}},
            {"continue": {"top_p": 1.1}}, {"continue": {"seed": True}},
            {"continue": {"stop": []}}, {"continue": {"thinking": {}}},
            {"continue": {"thinking": {"type": "disabled", "api_key": NESTED_SECRET}}},
            {"continue": {"thinking": {"type": "enabled"}, "reasoning_effort": "none"}},
            {"continue": {"thinking": {"type": "disabled"}, "reasoning_effort": "low"}},
        ]
        invalid.extend({"continue": {"max_tokens": value}}
                       for value in (0, -1, None, False, True, 1.5, 5000.0, "5000", 128_001))
        before = database_snapshot()
        credentials = self.credentials_snapshot()
        for index, value in enumerate(invalid):
            with self.subTest(case=index):
                status, rejected = self.request("PUT", "/api/ai/settings", {
                    "model": "synthetic-invalid-new-model", "api_key": "synthetic-invalid-new-key",
                    "task_parameters": value,
                })
                self.assertEqual(status, 400, rejected)
                self.assertEqual(database_snapshot(), before)
                self.assertEqual(self.credentials_snapshot(), credentials)
        REPORT["observations"].append({
            "test": self._testMethodName, "invalid_cases": len(invalid),
            "all_tables_and_schema_unchanged": database_snapshot() == before,
            "database_sha256_before": snapshot_digest(before),
            "database_sha256_after": snapshot_digest(database_snapshot()),
            "credential_files_unchanged": self.credentials_snapshot() == credentials,
        })
        self.assertEqual(len(provider_fixture.Handler.calls), self.calls_before)

    def test_04_task_reasoning_setting_replaces_inherited_alternative_representation(self):
        self.save(parameters={"max_tokens": 3000, "temperature": 0.7,
                              "thinking": {"type": "disabled"}},
                  task_parameters={"continue": {"reasoning_effort": "low"}})
        actual = self.preview()
        self.assertEqual(actual["reasoning_effort"], "low")
        self.assertNotIn("thinking", actual)
        self.save(parameters={"max_tokens": 3000, "temperature": 0.7, "reasoning_effort": "high"},
                  task_parameters={"continue": {"thinking": {"type": "disabled"}}})
        actual = self.preview()
        self.assertEqual(actual["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", actual)

    def test_05_aliases_replace_whole_objects_and_cleared_targets_restore_fallback(self):
        global_parameters = {"temperature": 1.4, "top_p": 0.92, "max_tokens": 3000}
        aliases = {"summary": {"temperature": 0.1, "top_p": 0.61, "max_tokens": 4200},
                   "check": {"temperature": 0.2, "top_p": 0.62, "max_tokens": 4300}}
        self.save(parameters=global_parameters, task_parameters=aliases)
        for task, source in (("chapter_analysis", "summary"), ("semantic_review", "check")):
            with self.subTest(task=task):
                actual = self.preview(task)
                for key, value in aliases[source].items():
                    self.assertEqual(actual[key], value)
        explicit = {**aliases, "chapter_analysis": {"max_tokens": 5100},
                    "semantic_review": {"temperature": 0}}
        saved = self.save(task_parameters=explicit)
        self.assertEqual(saved["task_parameters"], explicit)
        analysis = self.preview("chapter_analysis")
        self.assertEqual(analysis["max_tokens"], 5100)
        self.assertEqual(analysis["temperature"], 1.4)
        self.assertEqual(analysis["top_p"], 0.92)
        review = self.preview("semantic_review")
        self.assertEqual(review["temperature"], 0)
        self.assertEqual(review["max_tokens"], 3000)
        self.assertEqual(review["top_p"], 0.92)
        cleared = self.save(task_parameters={**aliases, "chapter_analysis": {}, "semantic_review": {}})
        self.assertEqual(cleared["task_parameters"], aliases)
        self.assertEqual(self.preview("chapter_analysis")["max_tokens"], 4200)
        self.assertEqual(self.preview("semantic_review")["max_tokens"], 4300)

    def test_06_explicit_output_limit_wins_for_preview_direct_and_queue_routes(self):
        self.save(parameters={"temperature": 1.1, "max_tokens": 3000},
                  task_parameters={"continue": {"temperature": 0, "max_tokens": 5100}})
        for maximum in (1, 128_000):
            with self.subTest(maximum=maximum):
                self.assertEqual(self.preview(max_tokens=maximum)["max_tokens"], maximum)
        self.assertEqual(self.preview()["max_tokens"], 5100)
        status, generated = self.request("POST", "/api/ai", {
            "task": "continue", "chapter_id": self.chapter_id, "max_tokens": 6100})
        self.assertEqual(status, 200, generated)
        self.assertEqual(provider_fixture.Handler.calls[-1]["payload"]["max_tokens"], 6100)
        self.assertEqual(provider_fixture.Handler.calls[-1]["payload"]["temperature"], 0)
        job = self.queue(max_tokens=6200)
        self.assertEqual(job["max_tokens"], 6200)
        self.assertEqual(self.process(job)["payload"]["max_tokens"], 6200)

    def test_07_deferred_queue_freezes_task_parameters_model_and_output_limit(self):
        self.save(parameters={"temperature": 1.2, "max_tokens": 3000},
                  models={"continue": "original-task-parameter-model"},
                  task_parameters={"continue": {"temperature": 0, "max_tokens": 5400, "seed": 17,
                                                "thinking": {"type": "disabled"}}})
        job = self.queue()
        original = self.job_audit(job)
        old_payload = copy.deepcopy(original["consent"]["request_audit"]["payload"])
        self.assertEqual(job["max_tokens"], 5400)
        self.save(model="replacement-task-parameter-model",
                  parameters={"temperature": 1.7, "max_tokens": 9000},
                  models={"continue": "replacement-purpose-model"},
                  task_parameters={"continue": {"temperature": 1.5, "max_tokens": 9100, "seed": 88,
                                                "reasoning_effort": "high"}})
        actual = self.process(job)
        self.assertEqual(actual["payload"], old_payload)
        self.assertEqual(actual["payload"]["temperature"], 0)
        self.assertEqual(actual["payload"]["max_tokens"], 5400)
        self.assertEqual(actual["payload"]["model"], "original-task-parameter-model")
        self.assertEqual(actual["payload"]["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", actual["payload"])
        final = self.job_audit(job)
        self.assertEqual(final["sampling"], original["sampling"])
        self.assertEqual(final["consent"]["provider_config"], original["consent"]["provider_config"])
        REPORT["observations"].append({
            "test": self._testMethodName, "original_payload_preserved": actual["payload"] == old_payload,
            "frozen_max_tokens": actual["payload"]["max_tokens"],
        })

    def test_08_canceled_original_retry_keeps_exact_task_payload_after_settings_change(self):
        self.save(task_parameters={"continue": {"temperature": 0, "max_tokens": 5600,
                                                "top_p": 0.7, "seed": 7}})
        job = self.queue()
        original = self.job_audit(job)
        old_payload = original["consent"]["request_audit"]["payload"]
        status, canceled = self.request("POST", f"/api/ai/jobs/{job['id']}/cancel", {})
        self.assertEqual((status, canceled["status"]), (200, "canceled"), canceled)
        self.save(parameters={"temperature": 1.5, "max_tokens": 8000},
                  task_parameters={"continue": {"temperature": 1.8, "max_tokens": 9200, "seed": 99}})
        before = len(provider_fixture.Handler.calls)
        status, retried = self.request("POST", f"/api/ai/jobs/{job['id']}/retry", {"mode": "original"})
        self.assertEqual(status, 202, retried)
        completed = self.wait_job(retried)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(provider_fixture.Handler.calls), before + 1)
        actual = provider_fixture.Handler.calls[-1]
        self.assertEqual(actual["payload"], old_payload)
        self.assertEqual(actual["raw"], server.json_text(old_payload).encode("utf-8"))
        final = self.job_audit(retried)
        self.assertEqual(final["consent"]["request_audit"]["request_sha256"],
                         original["consent"]["request_audit"]["request_sha256"])
        self.assertEqual(final["consent"]["provider_config"], original["consent"]["provider_config"])

    def test_09_unknown_completion_keeps_task_payload_and_refuses_both_retry_routes(self):
        self.settings("eof", key=SYNTHETIC_KEY, model="task-parameter-fixture")
        self.save(task_parameters={"continue": {"temperature": 0, "max_tokens": 5700}})
        job = self.queue()
        original = self.job_audit(job)
        status, failed = self.request("POST", f"/api/ai/jobs/{job['id']}/process", {})
        self.assertEqual(status, 200, failed)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["result"]["completion_state"], "unknown")
        self.assertEqual(provider_fixture.Handler.calls[-1]["payload"],
                         original["consent"]["request_audit"]["payload"])
        self.save(task_parameters={"continue": {"temperature": 1.7, "max_tokens": 9300}})
        before = database_snapshot()
        calls_before = len(provider_fixture.Handler.calls)
        for kind, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
            with self.subTest(route=kind):
                status, rejected = self.request("POST", f"/api/ai/{kind}/{identifier}/retry", {})
                self.assertEqual(status, 409, rejected)
                self.assertEqual(database_snapshot(), before)
                self.assertEqual(len(provider_fixture.Handler.calls), calls_before)
        REPORT["observations"].append({
            "test": self._testMethodName, "unknown_retry_dispatches": len(provider_fixture.Handler.calls) - calls_before,
            "all_tables_and_schema_unchanged": database_snapshot() == before,
        })

    def test_10_planning_callbacks_use_task_overrides_and_explicit_output_priority(self):
        self.save(parameters={"temperature": 1.2, "max_tokens": 3000},
                  task_parameters={"outline": {"temperature": 0, "max_tokens": 5800},
                                   "scene": {"temperature": 0.2, "max_tokens": 5900}})
        for stage, task, expected_maximum, expected_temperature, explicit in (
                ("overview", "outline", 5800, 0, None),
                ("scenes", "scene", 5900, 0.2, None),
                ("overview", "outline", 6900, 0, 6900),
                ("scenes", "scene", 7000, 0.2, 7000)):
            with self.subTest(stage=stage, explicit=explicit):
                job = self.planning_job(stage, maximum=explicit)
                self.assertEqual(job["task"], task)
                self.assertEqual(job["max_tokens"], expected_maximum)
                audit = self.job_audit(job)["consent"]["request_audit"]["payload"]
                self.assertEqual(audit["max_tokens"], expected_maximum)
                self.assertEqual(audit["temperature"], expected_temperature)
        self.assertEqual(len(provider_fixture.Handler.calls), self.calls_before)
        REPORT["observations"].append({
            "test": self._testMethodName, "real_callback_jobs": 4,
            "synthetic_later_stage_setup": True, "provider_dispatches": 0,
        })

    def test_11_real_zip_roundtrip_preserves_task_preferences_without_credentials(self):
        preferences = {
            "continue": {"temperature": 0, "max_tokens": 6000, "seed": 0,
                         "thinking": {"type": "disabled"}},
            "summary": {"temperature": 0.3, "max_tokens": 6100},
            "check": {"top_p": 0.71, "max_tokens": 6200},
        }
        self.save(task_parameters=preferences)
        status, created = self.request("POST", "/api/backups", {})
        self.assertEqual(status, 201, created)
        package = self.raw_zip(created["id"])
        stored = self.stored_zip_settings(package)
        self.assertEqual(stored["task_parameters"], preferences)
        self.assertIsNone(stored["_credential_ref"])
        restored = self.restore(package)
        self.assertEqual(restored["task_parameters"], preferences)
        self.assertFalse(restored["api_key_configured"])
        self.assertEqual(self.preview("chapter_analysis")["max_tokens"], 6100)
        self.assertEqual(self.preview("semantic_review")["max_tokens"], 6200)
        REPORT["observations"].append({
            "test": self._testMethodName, "known_task_preferences_preserved": True,
            "restored_credentials_independent": not restored["api_key_configured"],
            "archive_sha256": digest(package),
        })

    def test_12_legacy_zip_removes_nested_secrets_unknown_tasks_and_unknown_parameters(self):
        with server.connect() as conn:
            row = conn.execute("SELECT settings_json FROM app_model_settings WHERE project_id=?",
                               (self.project,)).fetchone()
            raw = json.loads(row["settings_json"])
            raw["api_key"] = NESTED_SECRET
            raw["task_parameters"] = {
                "continue": {"temperature": 0, "max_tokens": 6300, "api_key": NESTED_SECRET,
                             "thinking": {"type": "disabled", "api_key": NESTED_SECRET}},
                "unknown-task": {"api_key": NESTED_SECRET, "temperature": 0.2},
                "summary": {"top_p": 0.73, "max_tokens": 6400, "unknown": NESTED_SECRET},
            }
            conn.execute("UPDATE app_model_settings SET settings_json=? WHERE project_id=?",
                         (server.json_text(raw), self.project))
        status, created = self.request("POST", "/api/backups", {})
        self.assertEqual(status, 201, created)
        package = self.raw_zip(created["id"])
        stored = self.stored_zip_settings(package)
        expected = {"continue": {"temperature": 0, "max_tokens": 6300,
                                "thinking": {"type": "disabled"}},
                    "summary": {"top_p": 0.73, "max_tokens": 6400}}
        self.assertEqual(stored.get("task_parameters"), expected)
        self.assertNotIn(NESTED_SECRET, json.dumps(stored, ensure_ascii=False))
        restored = self.restore(package)
        self.assertEqual(restored["task_parameters"], expected)
        self.assertNotIn(NESTED_SECRET, json.dumps(restored, ensure_ascii=False))
        self.assertFalse(restored["api_key_configured"])
        REPORT["observations"].append({
            "test": self._testMethodName, "synthetic_nested_secret_removed": True,
            "supported_preferences_preserved": True, "restored_credentials_independent": True,
        })


def main():
    output = os.environ.get("MOYU_TASK_PARAMETER_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; choose a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TaskParameterHTTPTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful(), "methods": result.testsRun,
        "failures": [{"test": str(test), "traceback": trace} for test, trace in result.failures],
        "errors": [{"test": str(test), "traceback": trace} for test, trace in result.errors],
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "paid_provider_calls": 0, "external_model_calls": 0,
        "production_database_accessed": False, "real_credentials_read": False,
        "raw_database_values_saved": False,
        "source_snapshot_provenance": os.environ.get(
            "MOYU_TASK_PARAMETER_PROVENANCE", "current isolated test checkout"),
        "scope": "Temporary synthetic SQLite; real loopback application HTTP and free local provider.",
        "claim_limit": "Task parameter transport and data protection; no literary-quality or deployment claim.",
        **copy.deepcopy(REPORT),
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"http", "observations", "failures", "errors"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
