#!/usr/bin/env python3
"""Independent portable-ZIP HTTP checks using two synthetic data directories."""
from __future__ import annotations

import base64
import copy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from backend import moyu_backups as backups  # noqa: E402
from backend import moyu_portable as portable  # noqa: E402
from backend import moyu_workspace as workspace  # noqa: E402
from backend import server  # noqa: E402
from portable_backup_fixtures import (  # noqa: E402
    AGENT_TABLES, ALTERNATE_BRANCH, ALL_TABLES, CHAT_TABLES, SOURCE_ACTOR, SOURCE_BRANCH,
    SOURCE_PROJECT, seed_source,
)

TARGET_PROJECT = "portable-http-target"
TARGET_BRANCH = "portable-http-main"
TARGET_ACTOR = "portable-http-author"
TARGET_ALTERNATE_BRANCH = "portable-http-alternate"
SYNTHETIC_KEY = "synthetic-portable-http-credential"
SOURCES = (
    "backend/server.py", "backend/moyu_backups.py", "backend/moyu_portable.py", "backend/moyu_workspace.py",
    "backend/moyu_analysis.py", "backend/moyu_context.py", "backend/moyu_materials.py", "backend/moyu_import.py",
    "backend/moyu_planning.py", "backend/moyu_settings.py", "backend/moyu_records.py", "backend/moyu_revision.py",
    "backend/moyu_provider.py", "scripts/sqlite_backup.py",
    "backend/moyu_agent_checkpoint.py", "backend/moyu_agent_service.py", "backend/moyu_writing_agent.py", "requirements.txt",
    "backend/moyu_agent_chat.py", "backend/moyu_agent_settings.py",
    "docs/schema-v1.sql", "docs/schema-v1.1-migration.sql",
    "tests/portable_backup_fixtures.py", "tests/portable_backup_http_fixtures.py",
)
REPORT = {
    "passed": False, "synthetic_only": True,
    "scope": "Two isolated SQLite directories and real loopback HTTP handlers.",
    "claim_limit": "HTTP correctness only; no desktop, real data, literature or deployment claim.",
    "production_database_accessed": False, "real_credentials_read": False,
    "free_provider_calls": 0, "paid_provider_calls": 0,
    "external_model_calls": 0, "worker_calls": 0,
    "attempted_provider_calls": 0, "attempted_worker_calls": 0,
    "cases": [], "observations": [], "http": [], "execution_sentinels": [],
}


def digest(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def fingerprints():
    return {name: digest((ROOT / name).read_bytes()) for name in SOURCES}


def public_value(value):
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": digest(value)}
    if isinstance(value, str):
        if REPORT.get("temporary_root"):
            value = value.replace(REPORT["temporary_root"], "$fixture")
        return value.replace(str(ROOT), "$workspace")
    if isinstance(value, (tuple, list)):
        return [public_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(public_value(item) for item in value)
    if isinstance(value, dict):
        result = {name: public_value(item) for name, item in value.items()}
        if "data_base64" in result and isinstance(value["data_base64"], str):
            raw = value["data_base64"]
            result["data_base64"] = {
                "encoded_characters": len(raw), "encoded_sha256": digest(raw),
            }
        return result
    return value


def freeze_database(path):
    """Compare all rows and schema without a product capture/remap function."""
    with sqlite3.connect(path) as conn:
        schema = [tuple(row) for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )]
        rows = {}
        for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            try:
                rows[name] = [tuple(row) for row in conn.execute(
                    "SELECT rowid,* FROM " + quoted + " ORDER BY rowid")]
            except sqlite3.OperationalError as error:
                if "no such column: rowid" not in str(error):
                    raise
                keys = [row[1] for row in conn.execute("PRAGMA table_info(" + quoted + ")")
                        if row[5]]
                order = ",".join('"' + key.replace('"', '""') + '"' for key in keys)
                rows[name] = [tuple(row) for row in conn.execute(
                    "SELECT * FROM " + quoted + " ORDER BY " + order)]
        return {"schema": schema, "rows": rows,
                "user_version": conn.execute("PRAGMA user_version").fetchone()[0]}


def frozen_summary(value):
    return {
        "sha256": digest(encode(public_value(value))),
        "tables": len(value["rows"]),
        "rows": {name: len(items) for name, items in value["rows"].items()},
        "schema_sha256": digest(encode(value["schema"])),
        "user_version": value["user_version"],
    }


def decode_zip(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        project_raw = archive.read("project.json")
        return (json.loads(project_raw), json.loads(archive.read("manifest.json")),
                project_raw)


def make_zip(payload, *, manifest=None, project_raw=None, manifest_raw=None,
             duplicate=None, extras=(), compression=zipfile.ZIP_DEFLATED,
             member_mode=None):
    raw = encode(payload) if project_raw is None else project_raw
    values = {
        "id": "backup_portable_http_fixture",
        "project_id": payload["project_id"],
        "branch_id": next((row["id"] for row in payload["tables"]["branch"]
                           if row["status"] == "active"), SOURCE_BRANCH),
        "project_title": payload["tables"]["project"][0]["name"],
        "scope": "complete_project", "created_at": "2026-10-15T00:00:00+00:00",
        "sha256": digest(raw), "verified": True,
        "row_counts": {name: len(rows) for name, rows in payload["tables"].items()},
        "source_integrity": "ok",
        "excluded": ["credentials", "other_projects", "rebuildable_search_index"],
    }
    values.update(manifest or {})
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=compression) as archive:
            for name, body in (("project.json", raw),
                               ("manifest.json", manifest_raw or encode(values))):
                if duplicate == name:
                    archive.writestr(name, b'{"first_duplicate":true}')
                member = zipfile.ZipInfo(name)
                member.compress_type = compression
                if member_mode is not None:
                    member.create_system = 3
                    member.external_attr = member_mode << 16
                archive.writestr(member, body)
            for name, body in extras:
                archive.writestr(name, body)
    return output.getvalue()


def changed_header(raw, local_offset, central_offset, value):
    changed = bytearray(raw)
    for signature, offset in ((b"PK\x03\x04", local_offset),
                              (b"PK\x01\x02", central_offset)):
        start = changed.index(signature)
        struct.pack_into("<H", changed, start + offset, value)
    return bytes(changed)


def _serve_fixture(connection, path, project, branch, actor, credentials):
    """Run the actual handler; the private pipe only injects test failures."""
    server.DB_PATH = Path(path)
    server.PROJECT_ID, server.BRANCH_ID, server.ACTOR_ID = project, branch, actor
    server.SEARCH_INDEX_MODE = "none"
    os.environ.update({
        "MOYU_AI_API_KEY": "", "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "",
        "MOYU_SECRET_KEY": "", "MOYU_CREDENTIALS_DIR": credentials,
    })
    calls = {"provider": 0, "worker": 0}
    flags = {}
    holding, release = threading.Event(), threading.Event()

    def forbidden_provider(*args, **kwargs):
        calls["provider"] += 1
        raise AssertionError("synthetic HTTP fixture forbids provider dispatch")

    def forbidden_worker(*args, **kwargs):
        calls["worker"] += 1
        raise AssertionError("synthetic HTTP fixture forbids starting a worker")

    original_response = server.portable_restore_response
    original_record = server.record_portable_restore
    original_validate = backups.validate_payload
    original_monotonic = time.monotonic

    def response(conn, restored):
        if flags.pop("response_failure", False):
            raise RuntimeError("synthetic response generation failure")
        return original_response(conn, restored)

    def record(conn, key, receipt):
        original_record(conn, key, receipt)
        if flags.pop("record_failure", False):
            raise RuntimeError("synthetic receipt recording failure")
        if flags.pop("hold_record", False):
            holding.set()
            if not release.wait(15):
                raise RuntimeError("synthetic held restore timed out")

    def validate(conn, payload, **values):
        if flags.pop("hold_validation", False):
            holding.set()
            if not release.wait(15):
                raise RuntimeError("synthetic held archive validation timed out")
        return original_validate(conn, payload, **values)

    class FixtureConnection(sqlite3.Connection):
        def commit(self):
            if flags.get("commit_failure") and self.in_transaction:
                found = self.execute(
                    "SELECT 1 FROM event_log WHERE event_type='project.portable_restored'"
                ).fetchone()
                if found:
                    flags.pop("commit_failure")
                    raise sqlite3.OperationalError("synthetic commit failure")
            return super().commit()

    def connect(*, check_same_thread=True):
        conn = sqlite3.connect(server.DB_PATH, timeout=30, factory=FixtureConnection,
                               check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    class FixtureHandler(server.Handler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if (self.path == "/api/state"
                    and self.headers.get("X-Moyu-Project") in flags.get("state_fail_projects", [])):
                return self.send_json(503, {"error": "synthetic scoped state read unavailable"})
            return super().do_GET()

        def send_json(self, status, value):
            if (self.path == "/api/backups/import/apply" and status < 400
                    and flags.pop("drop_response", False)):
                self.finish_transaction(status)
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            return super().send_json(status, value)

    with ExitStack() as stack:
        stack.enter_context(patch.object(server, "connect", connect))
        stack.enter_context(patch.object(server, "call_ai_provider", forbidden_provider))
        stack.enter_context(patch.object(server, "start_ai_worker", forbidden_worker))
        stack.enter_context(patch.object(server, "portable_restore_response", response))
        stack.enter_context(patch.object(server, "record_portable_restore", record))
        stack.enter_context(patch.object(backups, "validate_payload", validate))
        stack.enter_context(patch.object(
            portable.time, "monotonic",
            lambda: original_monotonic() + flags.get("clock_offset", 0)))
        server.init_db()
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        connection.send({"base": "http://127.0.0.1:" + str(http.server_port)})
        try:
            while True:
                command = connection.recv()
                action = command["action"]
                if action == "stop":
                    http.shutdown()
                    thread.join(5)
                    connection.send({"calls": calls, "thread_stopped": not thread.is_alive()})
                    return
                if action == "status":
                    connection.send({"calls": calls, "pending": len(portable._PREVIEWS)})
                elif action == "expire":
                    with portable._LOCK:
                        portable._PREVIEWS[command["preview_id"]]["created"] = (
                            time.monotonic() - portable.PREVIEW_SECONDS - 1)
                    connection.send({"expired": True})
                elif action == "pending_bytes":
                    portable.MAX_PENDING_BYTES = command["value"]
                    connection.send({"updated": True})
                elif action == "archive_limit":
                    backups.MAX_ARCHIVE_BYTES = command["value"]
                    connection.send({"updated": True})
                elif action == "database":
                    server.DB_PATH = Path(command["path"])
                    connection.send({"updated": True})
                elif action == "failure":
                    flags[command["name"]] = True
                    connection.send({"updated": True})
                elif action == "state_failures":
                    flags["state_fail_projects"] = command["projects"]
                    connection.send({"updated": True})
                elif action == "advance_clock":
                    flags["clock_offset"] = flags.get("clock_offset", 0) + command["seconds"]
                    connection.send({"advanced_seconds": command["seconds"]})
                elif action == "wait_holding":
                    connection.send({"holding": holding.wait(5)})
                elif action == "release":
                    release.set()
                    connection.send({"released": True})
                elif action == "synthetic_environment":
                    os.environ.update({
                        "MOYU_AI_API_KEY": SYNTHETIC_KEY,
                        "MOYU_AI_BASE_URL": "http://127.0.0.1:9/v1",
                        "MOYU_AI_MODEL": "synthetic-unused-model",
                    })
                    connection.send({"configured": True})
                else:
                    raise AssertionError("unknown private fixture command")
        finally:
            http.server_close()
            connection.close()


class TemporaryHTTP:
    def __init__(self, path, project, branch, actor, credentials):
        self.label = (Path(path).parent.parent.name
                      if Path(path).parent.name == "data" else Path(path).parent.name)
        self.calls_recorded = False
        context = multiprocessing.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(
            target=_serve_fixture,
            args=(child, str(path), project, branch, actor, str(credentials)),
        )
        self.process.start()
        child.close()
        if not self.pipe.poll(15):
            self.stop()
            raise AssertionError("synthetic HTTP server did not start")
        self.base = self.pipe.recv()["base"]

    def control(self, action, **values):
        self.pipe.send({"action": action, **values})
        if not self.pipe.poll(20):
            raise AssertionError("synthetic HTTP control timed out: " + action)
        return self.pipe.recv()

    def stop(self):
        if self.process.is_alive():
            result = self.control("stop")
            if not self.calls_recorded:
                REPORT["execution_sentinels"].append({
                    "fixture": self.label, **result["calls"],
                })
                REPORT["attempted_provider_calls"] += result["calls"]["provider"]
                REPORT["attempted_worker_calls"] += result["calls"]["worker"]
                REPORT["worker_calls"] += result["calls"]["worker"]
                self.calls_recorded = True
            self.process.join(10)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(5)
                raise AssertionError("synthetic HTTP server did not stop")
            if self.process.exitcode != 0 or not result["thread_stopped"]:
                raise AssertionError("synthetic HTTP server exit failed")
            if any(result["calls"].values()):
                raise AssertionError("synthetic HTTP server attempted model execution")
        self.pipe.close()


# Extra structural columns in the fixture that do not declare SQL foreign keys.
IDENTITY_COLUMNS = {
    "branch": {"head_revision", "merge_base_revision"},
    "document_revision": {"actor_id"},
    "event_log": {"aggregate_id", "actor_id", "expected_head", "idempotency_key"},
    "deletion_event": {"aggregate_id", "actor_id"},
    "audit_event": {"actor_id", "target_id"},
    "candidate_memory": {"reviewed_by", "created_by"},
    "assertion": {"reviewed_by"},
    "character_state": {"event_id"},
    "merge_conflict": {"merge_base", "left_revision", "right_revision", "resolver"},
    "prov_entity": {"entity_ref"},
    "prov_edge": {"subject_id", "object_id"},
    "export_job": {"requested_by", "confirmed_by"},
    "app_ai_job": {"retry_of"},
    "app_candidate_application": {
        "source_revision_id", "result_revision_id", "decision_id"},
    "app_chapter_analysis": {
        "project_id", "branch_id", "chapter_id", "source_revision_id", "run_id"},
    "app_story_review": {
        "project_id", "branch_id", "chapter_id", "source_revision_id", "run_id"},
    "app_planning_workflow": {"project_id", "branch_id"},
    "app_planning_attempt": {"job_id"},
    "app_planning_event": {"attempt_id"},
    "app_planning_application": {"project_id", "branch_id"},
}
EVIDENCE_FIELDS = {
    "source_span_id", "source_chapter_id", "source_revision_id", "candidate_memory_id",
    "subject_entity_id", "entity_id", "from_entity_id", "to_entity_id", "event_id",
    "assertion_id", "character_state_id", "plot_id", "timeline_id", "decision_id",
}
PLANNING_TABLES = {
    "app_planning_workflow", "app_planning_attempt",
    "app_planning_event", "app_planning_application",
}


def replace_named(value, mapping, fields):
    changed = copy.deepcopy(value)
    for name in fields:
        if isinstance(changed.get(name), str) and changed[name] in mapping:
            changed[name] = mapping[changed[name]]
    return changed


def evidence_expected(value, mapping):
    if isinstance(value, list):
        return [evidence_expected(item, mapping) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    return {
        name: mapping.get(item, item) if name in EVIDENCE_FIELDS and isinstance(item, str)
        else evidence_expected(item, mapping)
        for name, item in value.items()
    }


def blueprint_expected(value, mapping):
    changed = copy.deepcopy(value)
    for volume in changed.get("volumes", []):
        for chapter in volume.get("chapters", []):
            if chapter.get("chapter_id"):
                chapter["chapter_id"] = mapping[chapter["chapter_id"]]
    return changed


def json_expected(table, column, raw, mapping):
    """Use explicit owner paths; never call a product identity remapper."""
    value = json.loads(raw)
    if column == "evidence_json" or (table == "candidate_memory" and column == "payload_json"):
        return evidence_expected(value, mapping)
    if table == "event_log" and column == "payload_json":
        return replace_named(value, mapping, {"chapter_id", "revision_id", "job_id", "decision_id"})
    if table == "plot_thread" and column == "payload_json":
        return evidence_expected(value, mapping)
    if table == "app_timeline_event" and column == "entity_ids_json":
        return [mapping[item] for item in value]
    if table == "app_entity_scope" and column == "scope_json":
        value["known_by"] = [mapping.get(item, item) for item in value["known_by"]]
        scope = value["knowledge_scope"]
        scope["branches"] = [mapping[item] for item in scope["branches"]]
        for name in ("project_id", "subject_entity_id"):
            scope[name] = mapping[scope[name]]
        return value
    if table == "character_state" and column == "knowledge_scope_json":
        value["known_by"] = [mapping[item] for item in value["known_by"]]
        return value
    if table == "app_blueprint" and column == "payload_json":
        return blueprint_expected(value, mapping)
    if table == "ai_run" and column == "context_item_ids_json":
        return [mapping.get(item, item) for item in value]
    if table == "ai_run" and column == "consent_json":
        if value.get("source"):
            value["source"] = replace_named(value["source"], mapping, {
                "project_id", "branch_id", "chapter_id", "source_revision_id"})
        if value.get("job_id"):
            value["job_id"] = mapping[value["job_id"]]
        if value.get("candidate_revision"):
            value["candidate_revision"] = replace_named(
                value["candidate_revision"], mapping, {"parent_run_id", "root_run_id"})
        value["restored_read_only"] = True
        return value
    if table == "app_ai_job" and column in {"context_json", "result_json"}:
        for name in ("source", "_source"):
            if value.get(name):
                value[name] = replace_named(value[name], mapping, {
                    "project_id", "branch_id", "chapter_id", "source_revision_id"})
        if value.get("current_chapter"):
            value["current_chapter"] = replace_named(
                value["current_chapter"], mapping, {"id", "revision_id"})
        for name in ("candidate_revision", "_candidate_revision"):
            if value.get(name):
                value[name] = replace_named(
                    value[name], mapping, {"parent_run_id", "root_run_id"})
        if value.get("project"):
            value["project"]["id"] = mapping[value["project"]["id"]]
        if value.get("_agent_chat"):
            value["_agent_chat"] = replace_named(
                value["_agent_chat"], mapping, {"chat_session_id", "chat_turn_id"})
        if value.get("blueprint"):
            value["blueprint"] = blueprint_expected(value["blueprint"], mapping)
        if value.get("planning_workflow"):
            value["planning_workflow"] = replace_named(
                value["planning_workflow"], mapping, {"id", "attempt_id"})
        if value.get("_material_source"):
            marker = value["_material_source"]
            marker["project_id"] = mapping[marker["project_id"]]
            marker["branch_id"] = mapping[marker["branch_id"]]
            for dependency in marker["dependencies"]:
                dependency["id"] = mapping[dependency["id"]]
        for passage in value.get("reference_passages", []):
            for name in ("document_id", "section_id", "project_id", "branch_id"):
                passage[name] = mapping[passage[name]]
            passage["id"] = (passage["section_id"] + ":" + str(passage["section_start"])
                             + ":" + str(passage["section_end"]))
        if column == "context_json":
            value["_restored_read_only"] = True
        return value
    if table in CHAT_TABLES:
        source_fields = {"project_id", "branch_id", "chapter_id", "source_revision_id"}
        if column == "references_json":
            return [replace_named(reference, mapping, source_fields) for reference in value]
        if column == "source_json":
            return replace_named(value, mapping, source_fields) if value is not None else None
        if column == "context_json":
            if value.get("project"):
                value["project"]["id"] = mapping[value["project"]["id"]]
            if value.get("current_chapter"):
                value["current_chapter"] = replace_named(
                    value["current_chapter"], mapping, {"id", "revision_id"})
            if value.get("_agent_chat"):
                value["_agent_chat"] = replace_named(
                    value["_agent_chat"], mapping, {"chat_session_id", "chat_turn_id"})
            return value
        if column == "result_json":
            return replace_named(
                value, mapping, {"writing_session_id", "chapter_id", "job_id"})
        return value
    if table in {"app_chapter_analysis", "app_story_review"} and column == "payload_json":
        value = replace_named(value, mapping, {
            "id", "project_id", "branch_id", "chapter_id", "source_revision_id",
            "revision_id", "run_id"})
        for dependency in value.get("source_dependencies", []):
            for name in ("chapter_id", "revision_id"):
                if dependency.get(name):
                    dependency[name] = mapping[dependency[name]]
        return evidence_expected(value, mapping)
    return value


class PortableBackupHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = ExitStack()
        cls.temp = Path(cls.shared.enter_context(
            tempfile.TemporaryDirectory(prefix="moyu-portable-http-")))
        REPORT["temporary_root"] = str(cls.temp)
        cls.shared.enter_context(patch.dict(os.environ, {
            "MOYU_AI_API_KEY": "", "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "",
            "MOYU_SECRET_KEY": "",
            "MOYU_CREDENTIALS_DIR": str(cls.temp / "never-real-credentials"),
        }))
        cls.parent_provider = cls.shared.enter_context(patch.object(
            server, "call_ai_provider", side_effect=AssertionError("provider forbidden")))
        cls.parent_worker = cls.shared.enter_context(patch.object(
            server, "start_ai_worker", side_effect=AssertionError("worker forbidden")))
        cls.source_path = cls.temp / "source" / "book.sqlite3"
        cls.source_credentials = cls.temp / "source" / "credentials"
        cls.source_credentials.mkdir(parents=True)
        with patch.object(server, "DB_PATH", cls.source_path), \
                patch.object(server, "PROJECT_ID", SOURCE_PROJECT), \
                patch.object(server, "BRANCH_ID", SOURCE_BRANCH), \
                patch.object(server, "ACTOR_ID", SOURCE_ACTOR):
            server.init_db()
            with server.connect() as conn:
                cls.identities = seed_source(conn)
        source = TemporaryHTTP(
            cls.source_path, SOURCE_PROJECT, SOURCE_BRANCH, SOURCE_ACTOR,
            cls.source_credentials)
        try:
            status, backup = cls.request(
                source.base, "POST", "/api/backups", {}, SOURCE_PROJECT, SOURCE_BRANCH)
            if status != 201:
                raise AssertionError((status, backup))
            status, cls.archive = cls.request(
                source.base, "GET", "/api/backups/" + backup["id"] + "/download",
                project=SOURCE_PROJECT, branch=SOURCE_BRANCH, binary=True)
            if status != 200:
                raise AssertionError((status, cls.archive))
            cls.payload, cls.manifest, cls.project_raw = decode_zip(cls.archive)
            if set(cls.payload["tables"]) != ALL_TABLES:
                raise AssertionError("source download must contain every captured table")
            if any(not rows for rows in cls.payload["tables"].values()):
                raise AssertionError("every captured table must have synthetic rows")
        finally:
            source.stop()
        cls.source_snapshot = freeze_database(cls.source_path)
        cls.source_file_sha256 = digest(cls.source_path.read_bytes())
        REPORT["source_download"] = {
            "source_stopped_before_target_start": True, "archive_bytes": len(cls.archive),
            "archive_sha256": digest(cls.archive), "project_sha256": digest(cls.project_raw),
            "all_captured_tables_nonempty": True,
            "row_counts": {name: len(rows) for name, rows in cls.payload["tables"].items()},
            "source_database": frozen_summary(cls.source_snapshot),
        }

    @classmethod
    def tearDownClass(cls):
        try:
            REPORT["attempted_provider_calls"] += cls.parent_provider.call_count
            REPORT["attempted_worker_calls"] += cls.parent_worker.call_count
            REPORT["worker_calls"] += cls.parent_worker.call_count
            if freeze_database(cls.source_path) != cls.source_snapshot:
                raise AssertionError("closed source database content changed")
            if digest(cls.source_path.read_bytes()) != cls.source_file_sha256:
                raise AssertionError("closed source database bytes changed")
            cls.parent_provider.assert_not_called()
            cls.parent_worker.assert_not_called()
            REPORT["closed_source_unchanged"] = True
        finally:
            cls.shared.close()

    @staticmethod
    def request(base, method, route, payload=None, project=TARGET_PROJECT,
                branch=TARGET_BRANCH, binary=False, raw=None, record=True):
        body = encode(payload) if payload is not None else raw
        request = urllib.request.Request(
            base + route, data=body, method=method,
            headers={"Content-Type": "application/json",
                     "X-Moyu-Project": project, "X-Moyu-Branch": branch})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                data = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            status, data = error.code, error.read()
        except (http.client.RemoteDisconnected, urllib.error.URLError, ConnectionResetError) as error:
            if record:
                REPORT["http"].append(public_value({
                    "method": method, "route": route, "project": project, "branch": branch,
                    "request": payload if payload is not None else raw,
                    "transport_error": type(error).__name__,
                }))
            raise
        result = data if binary else json.loads(data) if data else {}
        if record:
            REPORT["http"].append(public_value({
                "method": method, "route": route, "project": project, "branch": branch,
                "request": payload if payload is not None else raw,
                "status": status, "response": result,
            }))
        return status, result

    def setUp(self):
        self.target_root = self.temp / self._testMethodName
        self.path = self.target_root / "data" / "book.sqlite3"
        self.credentials = self.target_root / "credentials"
        self.credentials.mkdir(parents=True)
        (self.path.parent / "backups").mkdir(parents=True)
        with patch.object(server, "DB_PATH", self.path), \
                patch.object(server, "PROJECT_ID", TARGET_PROJECT), \
                patch.object(server, "BRANCH_ID", TARGET_BRANCH), \
                patch.object(server, "ACTOR_ID", TARGET_ACTOR):
            server.init_db()
            with server.connect() as conn:
                conn.execute(
                    "INSERT INTO branch(id,project_id,parent_id,status,acl_json) "
                    "VALUES(?,?,?,?,?)",
                    (TARGET_ALTERNATE_BRANCH, TARGET_PROJECT, TARGET_BRANCH, "active", "{}"))
                other = server.new_project(conn, {"title": "目标原有另一部作品"})
                self.other_project = other["project"]["id"]
                actual = conn.execute(
                    "SELECT id FROM branch WHERE project_id=?", (self.other_project,)
                ).fetchone()
                self.other_branch = actual[0]
                conn.execute(
                    "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status) "
                    "VALUES(?,?,?,?,?,?)",
                    ("portable-target-other-entity", self.other_project,
                     "人物", "目标另一书的人物", "[]", "active"))
                conn.commit()
        self.original = freeze_database(self.path)
        self.http = TemporaryHTTP(
            self.path, TARGET_PROJECT, TARGET_BRANCH, TARGET_ACTOR, self.credentials)
        self.assertEqual(freeze_database(self.path), self.original)
        self.assertEqual(list(self.credentials.iterdir()), [])
        self.assertEqual(list((self.path.parent / "backups").iterdir()), [])

    def tearDown(self):
        try:
            state = self.http.control("status")
            REPORT["observations"].append({
                "test": self._testMethodName,
                "attempted_provider_calls": state["calls"]["provider"],
                "attempted_worker_calls": state["calls"]["worker"],
                "target_database": frozen_summary(freeze_database(self.path)),
            })
            self.assertEqual(state["calls"], {"provider": 0, "worker": 0})
        finally:
            self.http.stop()

    def call(self, method, route, payload=None, **values):
        return self.request(self.http.base, method, route, payload, **values)

    def preview(self, raw=None, token=None, filename="完整作品备份🙂.zip", **values):
        request = {
            "filename": filename,
            "data_base64": base64.b64encode(self.archive if raw is None else raw).decode("ascii"),
        }
        if token is not None:
            request["preview_id"] = token
        status, result = self.call("POST", "/api/backups/import/preview", request, **values)
        self.assertEqual(status, 200, result)
        if token is not None:
            self.assertEqual(result["preview_id"], token)
        return result

    def confirmation(self, preview, title="独立恢复副本"):
        return {"preview_id": preview["preview_id"],
                "archive_sha256": preview["archive_sha256"], "title": title,
                "confirm": True}

    def restore(self, raw=None, title="独立恢复副本"):
        preview = self.preview(raw)
        status, result = self.call(
            "POST", "/api/backups/import/apply", self.confirmation(preview, title))
        self.assertEqual(status, 201, result)
        self.assertFalse(result["idempotent"])
        return result, preview

    def restore_status(self, request, **values):
        before = freeze_database(self.path)
        status, result = self.call("POST", "/api/backups/import/status", request, **values)
        self.assertEqual(status, 200, result)
        self.assert_zero_write(before)
        return result

    def assert_existing_unchanged(self, before=None):
        before = self.original if before is None else before
        after = freeze_database(self.path)
        self.assertEqual(after["schema"], before["schema"])
        self.assertEqual(after["user_version"], before["user_version"])
        self.assertEqual(set(after["rows"]), set(before["rows"]))
        for name, rows in before["rows"].items():
            self.assertEqual(after["rows"][name][:len(rows)], rows, name)

    def assert_zero_write(self, before):
        self.assertEqual(freeze_database(self.path), before)

    def assert_bad_preview(self, raw, allowed=(400,)):
        before = freeze_database(self.path)
        status, result = self.call("POST", "/api/backups/import/preview", {
            "filename": "bad-complete.zip",
            "data_base64": base64.b64encode(raw).decode("ascii"),
        })
        self.assertIn(status, allowed, result)
        self.assertTrue(result.get("error"), result)
        self.assert_zero_write(before)

    def assert_complete_restore(self, result, preview, payload=None, title="独立恢复副本"):
        payload = self.payload if payload is None else payload
        mapping = result["identity_map"]
        self.assertEqual(mapping[payload["project_id"]], result["project_id"])
        self.assertNotEqual(result["project_id"], payload["project_id"])
        ids = [row["id"] for rows in payload["tables"].values()
               for row in rows if row.get("id")]
        self.assertEqual(len(set(mapping[old] for old in ids)), len(set(ids)))
        self.assertFalse(set(ids) & {mapping[old] for old in ids})
        self.assertEqual(result["archive_sha256"], preview["archive_sha256"])
        self.assertEqual(result["project_sha256"], preview["project_sha256"])
        self.assertEqual(result["project"]["title"], title)
        self.assertTrue(math.isfinite(result["rto_seconds"]))
        self.assertGreaterEqual(result["rto_seconds"], 0)
        self.assertEqual(result["row_counts"], {
            name: len(rows) for name, rows in payload["tables"].items()})
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            for table, source_rows in payload["tables"].items():
                columns = conn.execute('PRAGMA table_info("' + table + '")').fetchall()
                keys = [column[1] for column in sorted(columns, key=lambda item: item[5])
                        if column[5]]
                references = {row[3] for row in conn.execute(
                    'PRAGMA foreign_key_list("' + table + '")')}
                references |= IDENTITY_COLUMNS.get(table, set()) | {"id"}
                matched_rows = []
                for source in source_rows:
                    expected = copy.deepcopy(source)
                    for name, value in source.items():
                        if isinstance(value, dict) and set(value) == {"$blob"}:
                            expected[name] = base64.b64decode(value["$blob"], validate=True)
                        elif name in references and isinstance(value, str):
                            expected[name] = mapping.get(value, value)
                    if table == "project":
                        expected["name"] = title
                    if table == "app_assistant_request":
                        expected["token_hash"] = digest(
                            result["project_id"] + ":" + source["token_hash"])
                    if table == "app_state_version":
                        expected["version"] += 1
                    if table == "app_ai_job" and source["status"] in {"queued", "running"}:
                        expected.update(status="failed", cancel_requested=1,
                                        error="从备份恢复；旧请求不会自动再次调用模型")
                    if table == "ai_run" and source["status"] in {"queued", "running"}:
                        expected.update(status="unknown",
                                        error="备份中的外部请求状态未知，未重复执行")
                    if table == "app_planning_workflow":
                        expected.update(
                            status="canceled", version=source["version"] + 1,
                            cancel_reason="从备份恢复的规划仅供阅读，请重新开始规划；旧模型请求不会自动再次执行。")
                    if (table == "app_planning_attempt"
                            and source["status"] in {"queued", "running", "unknown"}):
                        expected.update(
                            status="unknown",
                            error="备份中的规划模型请求费用与执行状态未知，未重复执行。")
                    if table == "app_agent_session":
                        expected.update(status="restored", restored_read_only=1)
                    if table == "app_agent_chat_session":
                        expected["restored_read_only"] = 1
                    if (table == "app_agent_chat_turn"
                            and source["status"] in {"queued", "running"}):
                        expected["status"] = "failed"
                    if table == "app_agent_chat_action" and source["status"] == "confirming":
                        expected["status"] = "proposed"
                    where = " AND ".join('"' + key + '"=?' for key in keys)
                    row = conn.execute('SELECT rowid,* FROM "' + table + '" WHERE ' + where,
                                       [expected[key] for key in keys]).fetchone()
                    self.assertIsNotNone(row, (table, keys))
                    actual = dict(row)
                    matched_rows.append(actual.pop("rowid"))
                    self.assertEqual(set(actual), set(expected), (table, "columns"))
                    for name, value in expected.items():
                        with self.subTest(table=table, column=name, row=source.get("id")):
                            if name.endswith("_json") and isinstance(value, str):
                                if (table in PLANNING_TABLES or table in AGENT_TABLES or (
                                        table == "event_log" and name == "payload_json"
                                        and source["event_type"] == "project.portable_restored")):
                                    self.assertEqual(actual[name], source[name])
                                else:
                                    self.assertEqual(
                                        json.loads(actual[name]),
                                        json_expected(table, name, source[name], mapping))
                            else:
                                self.assertEqual(actual[name], value)
                self.assertEqual(matched_rows, sorted(matched_rows), (table, "row order"))
            receipts = conn.execute(
                "SELECT * FROM event_log WHERE idempotency_key=?",
                ("portable_restore_" + preview["preview_id"],)).fetchall()
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["project_id"], result["project_id"])
            self.assertEqual(receipts[0]["event_type"], "project.portable_restored")
            saved = json.loads(receipts[0]["payload_json"])
            self.assertEqual(saved["target_scope"], {
                "project_id": TARGET_PROJECT, "branch_id": TARGET_BRANCH})
            self.assertEqual(saved["result"]["identity_map"], mapping)
            for node in payload["tables"]["document_node"]:
                old = [row["id"] for row in payload["tables"]["document_revision"]
                       if row["document_node_id"] == node["id"]]
                new = [row[0] for row in conn.execute(
                    "SELECT id FROM document_revision WHERE document_node_id=? ORDER BY rowid",
                    (mapping[node["id"]],))]
                self.assertEqual(new, [mapping[item] for item in old])
        self.assert_existing_unchanged()

    def test_complete_all_tables_restore_from_closed_source(self):
        before = freeze_database(self.path)
        preview = self.preview()
        self.assert_zero_write(before)
        self.assertEqual(preview["archive_sha256"], digest(self.archive))
        self.assertEqual(preview["project_sha256"], digest(self.project_raw))
        self.assertEqual(preview["counts"]["branches"], 2)
        self.assertEqual(preview["counts"]["chapters"], 3)
        self.assertEqual(set(preview["row_counts"]), ALL_TABLES)
        self.assertNotIn("identity_map", preview)
        self.assertNotIn("payload", preview)
        self.assertNotIn(SYNTHETIC_KEY, encode(preview).decode("utf-8"))
        request = self.confirmation(preview)
        status, result = self.call("POST", "/api/backups/import/apply", request)
        self.assertEqual(status, 201, result)
        self.assert_complete_restore(result, preview)
        self.assertEqual(list((self.path.parent / "backups").iterdir()), [])
        self.assertEqual(list(self.credentials.iterdir()), [])
        after = freeze_database(self.path)
        for unused in range(2):
            status, state = self.call(
                "GET", "/api/state", project=result["project_id"], branch=result["branch_id"])
            self.assertEqual(status, 200, state)
            self.assertEqual(state["chapters"][0]["body"], self.identities["body"])
        self.assert_zero_write(after)
        REPORT["observations"].append({
            "test": self._testMethodName, "all_tables": len(ALL_TABLES),
            "source_closed": True, "empty_backups_and_credentials": True,
            "row_fields_and_history_order_verified": True,
            "restored_project_id": result["project_id"],
        })

    def test_same_confirmation_receipt_and_changed_parameters(self):
        result, preview = self.restore()
        before = freeze_database(self.path)
        request = self.confirmation(preview)
        for unused in range(3):
            status, repeated = self.call("POST", "/api/backups/import/apply", request)
            self.assertEqual(status, 200, repeated)
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(repeated["project_id"], result["project_id"])
            self.assertEqual(repeated["identity_map"], result["identity_map"])
            self.assert_zero_write(before)
        for field, value in (("title", "不同的恢复名"), ("archive_sha256", "1" * 64)):
            with self.subTest(changed_field=field):
                for route in ("/api/backups/import/apply", "/api/backups/import/status"):
                    status, response = self.call("POST", route, {**request, field: value})
                    self.assertEqual(status, 409, response)
                    self.assert_zero_write(before)
        checked = self.restore_status(request)
        self.assertTrue(checked["saved"])
        self.assertEqual(checked["result"]["project_id"], result["project_id"])
        self.assertEqual(checked["result"]["identity_map"], result["identity_map"])

    def test_status_preview_cancel_expiry_and_restart_have_zero_writes(self):
        before = freeze_database(self.path)
        preview = self.preview()
        request = self.confirmation(preview)
        self.assertEqual(self.restore_status(request), {
            "saved": False, "preview_available": True,
        })
        status, canceled = self.call(
            "POST", "/api/backups/import/cancel", {"preview_id": preview["preview_id"]})
        self.assertEqual(status, 200, canceled)
        self.assertEqual(self.restore_status(request), {
            "saved": False, "preview_available": False,
        })
        expired = self.preview()
        self.http.control("expire", preview_id=expired["preview_id"])
        self.assertEqual(self.restore_status(self.confirmation(expired)), {
            "saved": False, "preview_available": False,
        })
        unsaved = self.preview()
        self.http.stop()
        self.http = TemporaryHTTP(
            self.path, TARGET_PROJECT, TARGET_BRANCH, TARGET_ACTOR, self.credentials)
        self.assertEqual(self.restore_status(self.confirmation(unsaved)), {
            "saved": False, "preview_available": False,
        })
        self.assert_zero_write(before)

    def test_client_preview_id_is_idempotent_and_bound_to_original_upload(self):
        token = "restore_preview_" + "a" * 32
        first = self.preview(token=token)
        before = freeze_database(self.path)
        for unused in range(3):
            repeated = self.preview(token=token)
            for field in ("preview_id", "archive_sha256", "project_sha256",
                          "expires_at", "source", "row_counts"):
                self.assertEqual(repeated[field], first[field], field)
            self.assertEqual(self.http.control("status")["pending"], 1)
            self.assert_zero_write(before)
        changed = make_zip(self.payload)
        self.assertNotEqual(digest(changed), digest(self.archive))
        request = {
            "filename": "完整作品备份🙂.zip",
            "data_base64": base64.b64encode(self.archive).decode("ascii"),
            "preview_id": token,
        }
        values = (
            ({**request, "filename": "另一个文件名.zip"}, TARGET_PROJECT, TARGET_BRANCH),
            ({**request, "data_base64": base64.b64encode(changed).decode("ascii")},
             TARGET_PROJECT, TARGET_BRANCH),
            (request, self.other_project, self.other_branch),
            (request, TARGET_PROJECT, TARGET_ALTERNATE_BRANCH),
        )
        for number, (body, project, branch) in enumerate(values):
            with self.subTest(upload_conflict=number):
                status, result = self.call(
                    "POST", "/api/backups/import/preview", body, project=project, branch=branch)
                self.assertEqual(status, 409, result)
                self.assertEqual(self.http.control("status")["pending"], 1)
                self.assert_zero_write(before)
        status, restored = self.call(
            "POST", "/api/backups/import/apply", self.confirmation(first))
        self.assertEqual(status, 201, restored)
        self.assertEqual(self.http.control("status")["pending"], 0)
        self.assert_existing_unchanged()

    def test_concurrent_client_preview_id_uses_one_pending_slot(self):
        token = "restore_preview_" + "b" * 32
        before = freeze_database(self.path)
        barrier = threading.Barrier(4)

        def upload():
            barrier.wait(10)
            return self.preview(token=token)

        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda unused: upload(), range(4)))
        self.assertEqual({value["preview_id"] for value in results}, {token})
        self.assertEqual(len({value["expires_at"] for value in results}), 1)
        self.assertEqual(self.http.control("status")["pending"], 1)
        self.assert_zero_write(before)
        status, canceled = self.call("POST", "/api/backups/import/cancel", {"preview_id": token})
        self.assertEqual(status, 200, canceled)
        self.assertEqual(self.http.control("status")["pending"], 0)
        self.assert_zero_write(before)

    def test_cancel_before_late_preview_holds_token_for_thirty_minutes(self):
        token = "restore_preview_" + "c" * 32
        before = freeze_database(self.path)
        status, canceled = self.call("POST", "/api/backups/import/cancel", {"preview_id": token})
        self.assertEqual(status, 200, canceled)
        request = {
            "filename": "关闭后迟到的完整备份.zip",
            "data_base64": base64.b64encode(self.archive).decode("ascii"),
            "preview_id": token,
        }
        for seconds in (0, portable.PREVIEW_SECONDS - 1):
            with self.subTest(elapsed_seconds=seconds):
                if seconds:
                    self.http.control("advance_clock", seconds=seconds)
                status, rejected = self.call("POST", "/api/backups/import/preview", request)
                self.assertEqual(status, 409, rejected)
                self.assertEqual(self.http.control("status")["pending"], 0)
                self.assert_zero_write(before)
        self.assertEqual(self.restore_status({
            "preview_id": token, "archive_sha256": digest(self.archive),
            "title": "不应建立的副本", "confirm": True,
        }), {"saved": False, "preview_available": False})
        self.http.control("advance_clock", seconds=2)
        valid = self.preview(token=token, filename=request["filename"])
        self.assertEqual(self.http.control("status")["pending"], 1)
        self.assertEqual(valid["archive_sha256"], digest(self.archive))
        self.assert_zero_write(before)
        status, canceled = self.call("POST", "/api/backups/import/cancel", {"preview_id": token})
        self.assertEqual(status, 200, canceled)
        self.assertEqual(self.http.control("status")["pending"], 0)
        self.assert_zero_write(before)
        REPORT["observations"].append({
            "test": self._testMethodName,
            "cancel_tombstone_seconds": portable.PREVIEW_SECONDS,
            "clock_advanced_synthetically": True,
            "late_upload_does_not_reoccupy_slot": True,
        })

    def test_cancel_during_validation_rejects_late_preview_without_new_slot(self):
        token = "restore_preview_" + "d" * 32
        before = freeze_database(self.path)
        request = {
            "filename": "校验中关闭的完整备份.zip",
            "data_base64": base64.b64encode(self.archive).decode("ascii"),
            "preview_id": token,
        }
        self.http.control("failure", name="hold_validation")
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(self.call, "POST", "/api/backups/import/preview", request)
            try:
                self.assertTrue(self.http.control("wait_holding")["holding"])
                self.assert_zero_write(before)
                status, canceled = self.call(
                    "POST", "/api/backups/import/cancel", {"preview_id": token})
                self.assertEqual(status, 200, canceled)
                self.assert_zero_write(before)
            finally:
                self.http.control("release")
            status, rejected = future.result(timeout=20)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.http.control("status")["pending"], 0)
        self.assert_zero_write(before)
        status, rejected = self.call("POST", "/api/backups/import/preview", request)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self.http.control("status")["pending"], 0)
        self.assert_zero_write(before)
        fresh = self.preview(token="restore_preview_" + "e" * 32)
        self.assertEqual(self.http.control("status")["pending"], 1)
        status, canceled = self.call(
            "POST", "/api/backups/import/cancel", {"preview_id": fresh["preview_id"]})
        self.assertEqual(status, 200, canceled)
        self.assert_zero_write(before)

    def test_concurrent_confirmations_only_one_project(self):
        preview = self.preview()
        request = self.confirmation(preview)
        barrier = threading.Barrier(6)

        def confirm():
            barrier.wait(10)
            return self.call("POST", "/api/backups/import/apply", request)

        with ThreadPoolExecutor(max_workers=6) as workers:
            results = list(workers.map(lambda unused: confirm(), range(6)))
        self.assertEqual(sorted(status for status, body in results), [200] * 5 + [201], results)
        self.assertEqual(len({body["project_id"] for status, body in results}), 1)
        self.assertEqual(sum(not body["idempotent"] for status, body in results), 1)
        self.assert_existing_unchanged()
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project").fetchone()[0], 3)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE event_type='project.portable_restored'"
            ).fetchone()[0], 1)

    def test_completed_receipt_survives_actual_server_restart(self):
        result, preview = self.restore()
        before = freeze_database(self.path)
        self.http.stop()
        self.http = TemporaryHTTP(
            self.path, TARGET_PROJECT, TARGET_BRANCH, TARGET_ACTOR, self.credentials)
        self.assertEqual(self.http.control("status")["pending"], 0)
        status, repeated = self.call(
            "POST", "/api/backups/import/apply", self.confirmation(preview))
        self.assertEqual(status, 200, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["project_id"], result["project_id"])
        self.assertEqual(repeated["identity_map"], result["identity_map"])
        self.assert_zero_write(before)
        checked = self.restore_status(self.confirmation(preview))
        self.assertTrue(checked["saved"])
        self.assertEqual(checked["result"]["project_id"], result["project_id"])
        self.assertEqual(checked["result"]["identity_map"], result["identity_map"])

    def test_success_response_lost_retries_saved_receipt(self):
        preview = self.preview()
        request = self.confirmation(preview)
        self.http.control("failure", name="drop_response")
        with self.assertRaises((http.client.RemoteDisconnected, urllib.error.URLError,
                                ConnectionResetError)):
            self.call("POST", "/api/backups/import/apply", request)
        after = freeze_database(self.path)
        status, canceled = self.call(
            "POST", "/api/backups/import/cancel", {"preview_id": preview["preview_id"]})
        self.assertEqual(status, 200, canceled)
        self.assert_zero_write(after)
        checked = self.restore_status(request)
        self.assertTrue(checked["saved"])
        status, repeated = self.call("POST", "/api/backups/import/apply", request)
        self.assertEqual(status, 200, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(checked["result"]["project_id"], repeated["project_id"])
        self.assertEqual(checked["result"]["identity_map"], repeated["identity_map"])
        self.assert_zero_write(after)
        self.assert_complete_restore(repeated, preview)

    def test_state_read_failures_do_not_recreate_saved_book(self):
        result, preview = self.restore()
        before = freeze_database(self.path)
        scopes = ((TARGET_PROJECT, TARGET_BRANCH),
                  (result["project_id"], result["branch_id"]))
        for unavailable in ([TARGET_PROJECT], [result["project_id"]],
                            [TARGET_PROJECT, result["project_id"]]):
            with self.subTest(unavailable_scopes=unavailable):
                self.http.control("state_failures", projects=unavailable)
                for project, branch in scopes:
                    status, state = self.call("GET", "/api/state", project=project, branch=branch)
                    self.assertEqual(status, 503 if project in unavailable else 200, state)
                    self.assert_zero_write(before)
                checked = self.restore_status(self.confirmation(preview))
                self.assertTrue(checked["saved"])
                self.assertEqual(checked["result"]["project_id"], result["project_id"])
                status, repeated = self.call(
                    "POST", "/api/backups/import/apply", self.confirmation(preview))
                self.assertEqual(status, 200, repeated)
                self.assertEqual(repeated["project_id"], result["project_id"])
                self.assert_zero_write(before)
        self.http.control("state_failures", projects=[])

    def test_saved_receipt_response_generation_failure_is_read_only(self):
        result, preview = self.restore()
        before = freeze_database(self.path)
        for route in ("/api/backups/import/apply", "/api/backups/import/status"):
            with self.subTest(route=route):
                self.http.control("failure", name="response_failure")
                status, failure = self.call(
                    "POST", route, self.confirmation(preview))
                self.assertEqual(status, 500, failure)
                self.assert_zero_write(before)
                checked = self.restore_status(self.confirmation(preview))
                self.assertTrue(checked["saved"])
                self.assertEqual(checked["result"]["project_id"], result["project_id"])

    def test_precommit_response_receipt_and_commit_failure_roll_back(self):
        for failure in ("response_failure", "record_failure", "commit_failure"):
            with self.subTest(failure=failure):
                preview = self.preview()
                before = freeze_database(self.path)
                self.http.control("failure", name=failure)
                status, response = self.call(
                    "POST", "/api/backups/import/apply", self.confirmation(preview))
                self.assertEqual(status, 500, response)
                self.assert_zero_write(before)
                self.assertEqual(self.restore_status(self.confirmation(preview)), {
                    "saved": False, "preview_available": True,
                })
                status, restored = self.call(
                    "POST", "/api/backups/import/apply", self.confirmation(preview))
                self.assertEqual(status, 201, restored)
                self.assert_existing_unchanged()

    def test_insert_trigger_failure_rolls_back_all_restored_rows(self):
        preview = self.preview()
        source_ids = ",".join("'" + row["id"].replace("'", "''") + "'"
                              for row in self.payload["tables"]["app_reference_section"])
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TRIGGER synthetic_restore_failure BEFORE INSERT ON app_reference_section "
                "WHEN NEW.id NOT IN (" + source_ids + ") "
                "BEGIN SELECT RAISE(ABORT,'synthetic middle-table insert failure'); END")
        before = freeze_database(self.path)
        status, result = self.call(
            "POST", "/api/backups/import/apply", self.confirmation(preview))
        self.assertIn(status, (400, 409), result)
        self.assert_zero_write(before)
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER synthetic_restore_failure")
        status, result = self.call(
            "POST", "/api/backups/import/apply", self.confirmation(preview))
        self.assertEqual(status, 201, result)
        self.assert_existing_unchanged()
        REPORT["observations"].append({
            "test": self._testMethodName,
            "fault_stage": "target insert after identity remapping",
            "scratch_validation_original_ids_allowed": True,
            "target_remapped_insert_rejected_then_all_rows_rolled_back": True,
        })

    def test_cancel_expiry_and_pending_slot_release_are_read_only(self):
        before = freeze_database(self.path)
        first, second = self.preview(), self.preview()
        self.assertEqual(self.http.control("status")["pending"], 2)
        self.assert_bad_preview(self.archive)
        status, canceled = self.call(
            "POST", "/api/backups/import/cancel", {"preview_id": first["preview_id"]})
        self.assertEqual(status, 200, canceled)
        self.assert_zero_write(before)
        status, rejected = self.call(
            "POST", "/api/backups/import/apply", self.confirmation(first))
        self.assertEqual(status, 400, rejected)
        self.assert_zero_write(before)
        third = self.preview()
        self.http.control("expire", preview_id=second["preview_id"])
        status, rejected = self.call(
            "POST", "/api/backups/import/apply", self.confirmation(second))
        self.assertEqual(status, 400, rejected)
        self.assert_zero_write(before)
        fourth = self.preview()
        for selected in (third, fourth):
            status, canceled = self.call(
                "POST", "/api/backups/import/cancel", {"preview_id": selected["preview_id"]})
            self.assertEqual(status, 200, canceled)
        self.assertEqual(self.http.control("status")["pending"], 0)
        self.assert_zero_write(before)

    def test_pending_byte_limit_at_boundary_with_small_synthetic_budget(self):
        self.assertEqual(portable.MAX_PENDING_BYTES, 64 * 1024 * 1024)
        self.assertEqual(portable.MAX_PENDING, 2)
        self.assertEqual(portable.PREVIEW_SECONDS, 1800)
        before = freeze_database(self.path)
        self.http.control("pending_bytes", value=len(self.archive) * 2 - 1)
        first = self.preview()
        self.assert_bad_preview(self.archive)
        self.call("POST", "/api/backups/import/cancel", {"preview_id": first["preview_id"]})
        self.http.control("pending_bytes", value=len(self.archive) * 2)
        first, second = self.preview(), self.preview()
        self.assertEqual(self.http.control("status")["pending"], 2)
        for selected in (first, second):
            self.call("POST", "/api/backups/import/cancel", {"preview_id": selected["preview_id"]})
        self.assert_zero_write(before)
        REPORT["observations"].append({
            "test": self._testMethodName,
            "configured_real_pending_bytes": 64 * 1024 * 1024,
            "tested_scaled_pending_bytes": len(self.archive) * 2,
            "capacity_test_uses_small_synthetic_budget": True,
        })

    def test_archive_and_http_request_size_limits(self):
        self.assertEqual(backups.MAX_ARCHIVE_BYTES, 32 * 1024 * 1024)
        before = freeze_database(self.path)
        self.http.control("archive_limit", value=len(self.archive) - 1)
        self.assert_bad_preview(self.archive)
        self.http.control("archive_limit", value=len(self.archive))
        valid = self.preview()
        self.call("POST", "/api/backups/import/cancel", {"preview_id": valid["preview_id"]})
        self.http.control("archive_limit", value=32 * 1024 * 1024)
        self.assert_bad_preview(b"x" * (32 * 1024 * 1024 + 1))
        port = int(self.http.base.rsplit(":", 1)[-1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.putrequest("POST", "/api/backups/import/preview")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("X-Moyu-Project", TARGET_PROJECT)
            connection.putheader("X-Moyu-Branch", TARGET_BRANCH)
            connection.putheader("Content-Length", str(48 * 1024 * 1024 + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertTrue(json.loads(response.read()).get("error"))
        finally:
            connection.close()
        self.assert_zero_write(before)

    def test_scope_binding_before_and_after_saved_receipt(self):
        preview = self.preview()
        request = self.confirmation(preview)
        before = freeze_database(self.path)
        for project, branch in ((self.other_project, self.other_branch),
                                (TARGET_PROJECT, TARGET_ALTERNATE_BRANCH)):
            for route, body in (("/api/backups/import/apply", request),
                                ("/api/backups/import/status", request),
                                ("/api/backups/import/cancel",
                                 {"preview_id": preview["preview_id"]})):
                with self.subTest(project=project, branch=branch, route=route):
                    status, rejected = self.call(
                        "POST", route, body, project=project, branch=branch)
                    self.assertEqual(status, 409, rejected)
                    self.assert_zero_write(before)
        status, restored = self.call("POST", "/api/backups/import/apply", request)
        self.assertEqual(status, 201, restored)
        before = freeze_database(self.path)
        for route in ("/api/backups/import/apply", "/api/backups/import/status"):
            status, rejected = self.call(
                "POST", route, request,
                project=restored["project_id"], branch=restored["branch_id"])
            self.assertEqual(status, 409, rejected)
            self.assert_zero_write(before)

    def test_same_scope_token_is_bound_to_target_database_path(self):
        preview = self.preview()
        alternate_path = self.target_root / "another-data" / "book.sqlite3"
        alternate_path.parent.mkdir()
        with sqlite3.connect(self.path) as original, sqlite3.connect(alternate_path) as other:
            original.backup(other)
        before_original = freeze_database(self.path)
        before_other = freeze_database(alternate_path)
        self.http.control("database", path=str(alternate_path))
        try:
            for route, body in (
                    ("/api/backups/import/apply", self.confirmation(preview)),
                    ("/api/backups/import/status", self.confirmation(preview)),
                    ("/api/backups/import/cancel", {"preview_id": preview["preview_id"]})):
                with self.subTest(route=route):
                    status, rejected = self.call("POST", route, body)
                    self.assertEqual(status, 409, rejected)
                    self.assertEqual(freeze_database(alternate_path), before_other)
                    self.assertEqual(freeze_database(self.path), before_original)
        finally:
            self.http.control("database", path=str(self.path))

    def test_cancel_while_restore_saves_preserves_single_result(self):
        preview = self.preview()
        request = self.confirmation(preview)
        before = freeze_database(self.path)
        self.http.control("failure", name="hold_record")
        with ThreadPoolExecutor(max_workers=2) as workers:
            future = workers.submit(
                self.call, "POST", "/api/backups/import/apply", request)
            self.assertTrue(self.http.control("wait_holding")["holding"])
            status_future = workers.submit(
                self.call, "POST", "/api/backups/import/status", request)
            with self.assertRaises(FutureTimeoutError):
                status_future.result(timeout=0.2)
            self.assert_zero_write(before)
            status, rejected = self.call(
                "POST", "/api/backups/import/cancel", {"preview_id": preview["preview_id"]})
            self.assertEqual(status, 409, rejected)
            self.http.control("release")
            status, result = future.result(timeout=20)
            status_code, checked = status_future.result(timeout=20)
        self.assertEqual(status, 201, result)
        self.assertEqual(status_code, 200, checked)
        self.assertTrue(checked["saved"])
        self.assertEqual(checked["result"]["project_id"], result["project_id"])
        self.assertEqual(checked["result"]["identity_map"], result["identity_map"])
        self.restore_status(request)
        self.assert_existing_unchanged()

    def test_preview_contract_and_strict_request_json(self):
        good = {"filename": "valid.zip",
                "data_base64": base64.b64encode(self.archive).decode("ascii")}
        values = [
            {**good, "filename": "book.json"}, {**good, "filename": "../book\n.zip"},
            {**good, "filename": "a" * 501 + ".zip"},
            {**good, "data_base64": "invalid@@@"}, {**good, "data_base64": ""},
            {**good, "payload": self.payload}, {**good, "data_base64": None},
            {}, {"filename": "valid.zip"},
            {**good, "preview_id": None}, {**good, "preview_id": True},
            {**good, "preview_id": "client-created-token"},
            {**good, "preview_id": "restore_preview_" + "F" * 32},
            {**good, "preview_id": "restore_preview_" + "f" * 33},
        ]
        before = freeze_database(self.path)
        for number, value in enumerate(values):
            with self.subTest(invalid_request=number):
                status, rejected = self.call("POST", "/api/backups/import/preview", value)
                self.assertEqual(status, 400, rejected)
                self.assert_zero_write(before)
        for raw in (b"[]", b'{"filename":"a.zip","filename":"b.zip","data_base64":""}',
                    b'{"filename":"a.zip","data_base64":NaN}', b"\xff",
                    b'{"filename":"a.zip","data_base64":"\\ud800"}'):
            with self.subTest(raw_request=digest(raw)):
                status, rejected = self.call(
                    "POST", "/api/backups/import/preview", raw=raw)
                self.assertEqual(status, 400, rejected)
                self.assert_zero_write(before)

    def test_confirmation_contract_and_unicode_name_boundary(self):
        preview = self.preview()
        good = self.confirmation(preview)
        before = freeze_database(self.path)
        for field, value in (
                ("confirm", False), ("confirm", 1), ("title", ""),
                ("title", " "), ("title", "x\n"), ("title", "🙂" * 201),
                ("title", None), ("archive_sha256", "invalid"),
                ("preview_id", "client-created-token")):
            with self.subTest(field=field, value_type=type(value).__name__):
                for route in ("/api/backups/import/apply", "/api/backups/import/status"):
                    status, rejected = self.call("POST", route, {**good, field: value})
                    self.assertEqual(status, 400, rejected)
                    self.assert_zero_write(before)
        for value in ({**good, "payload": self.payload}, {key: item for key, item in good.items()
                                                       if key != "confirm"}):
            with self.subTest(extra_or_missing_field=set(value)):
                for route in ("/api/backups/import/apply", "/api/backups/import/status"):
                    status, rejected = self.call("POST", route, value)
                    self.assertEqual(status, 400, rejected)
                    self.assert_zero_write(before)
        status, rejected = self.call(
            "POST", "/api/backups/import/apply", {**good, "archive_sha256": "1" * 64})
        self.assertEqual(status, 409, rejected)
        self.assert_zero_write(before)
        status, restored = self.call(
            "POST", "/api/backups/import/apply", {**good, "title": "🙂" * 200})
        self.assertEqual(status, 201, restored)
        self.assertEqual(restored["project"]["title"], "🙂" * 200)
        self.assert_existing_unchanged()

    def test_bad_zip_members_crc_compression_and_expansion(self):
        raw = make_zip(self.payload, compression=zipfile.ZIP_STORED)
        corrupted = bytearray(raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.getinfo("project.json")
            filename, extra = struct.unpack_from("<HH", raw, info.header_offset + 26)
            corrupted[info.header_offset + 30 + filename + extra + 10] ^= 1
        cases = {
            "not_zip": b"plain text", "truncated": raw[:100], "empty": b"",
            "duplicate_project": make_zip(self.payload, duplicate="project.json"),
            "duplicate_manifest": make_zip(self.payload, duplicate="manifest.json"),
            "extra_member": make_zip(self.payload, extras=(("extra.txt", b"x"),)),
            "directory": make_zip(self.payload, extras=(("folder/", b""),)),
            "symlink": make_zip(self.payload, member_mode=stat.S_IFLNK | 0o777),
            "encrypted": changed_header(raw, 6, 8, 1),
            "unsupported_compression": changed_header(raw, 8, 10, 99),
            "crc": bytes(corrupted),
        }
        excessive = bytearray(raw)
        for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
            struct.pack_into("<I", excessive, excessive.index(signature) + offset, 256_000_001)
        cases["expanded_limit"] = bytes(excessive)
        for name, value in cases.items():
            with self.subTest(zip_issue=name):
                self.assert_bad_preview(value)

    def test_bad_json_versions_shapes_tables_and_columns(self):
        malformed = [
            make_zip(self.payload, project_raw=b"[]"),
            make_zip(self.payload, project_raw=b'{"format":1,"format":2}'),
            make_zip(self.payload, project_raw=b'{"version":NaN}'),
            make_zip(self.payload, project_raw=b"\xff"),
            make_zip(self.payload, manifest_raw=b"[]"),
            make_zip(self.payload, manifest_raw=b'{"scope":1,"scope":2}'),
            make_zip(self.payload, manifest_raw=b'{"scope":Infinity}'),
        ]
        for number, raw in enumerate(malformed):
            with self.subTest(raw_json=number):
                self.assert_bad_preview(raw)
        cases = []
        for name, value in (("version", True), ("version", 2), ("schema_version", 999),
                            ("schema_version", True), ("format", "other-project-format")):
            payload = copy.deepcopy(self.payload)
            payload[name] = value
            cases.append((name + "-" + str(value), payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["project"][0]["schema_version"] = 999
        cases.append(("project_schema", payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"].pop("app_entity_meta")
        cases.append(("missing_required_table", payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["unknown_persistent_table"] = []
        cases.append(("unknown_table", payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["project"][0]["unknown_column"] = "not allowed"
        cases.append(("unknown_column", payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["entity"][0] = "not a row"
        cases.append(("row_type", payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["entity"] = {}
        cases.append(("table_type", payload))
        for name, payload in cases:
            with self.subTest(json_issue=name):
                self.assert_bad_preview(make_zip(payload))

    def test_manifest_binding_and_row_counts(self):
        cases = {
            "project": {"project_id": "another-project"},
            "branch": {"branch_id": "another-branch"},
            "title": {"project_title": "forged title"},
            "scope": {"scope": "partial_project"},
            "sha": {"sha256": "1" * 64},
            "counts": {"row_counts": {name: 0 for name in self.payload["tables"]}},
        }
        for name, change in cases.items():
            with self.subTest(manifest_issue=name):
                self.assert_bad_preview(make_zip(self.payload, manifest=change))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["project"][0]["id"] = "another-project"
        self.assert_bad_preview(make_zip(payload))

    def test_closed_graph_missing_and_other_target_book_references(self):
        with sqlite3.connect(self.path) as conn:
            target_entity = conn.execute(
                "SELECT id FROM entity WHERE project_id=?", (self.other_project,)
            ).fetchone()[0]
        cases = []
        for table, field, value in (
                ("app_story_relation", "from_entity_id", target_entity),
                ("document_revision", "document_node_id", "missing-chapter"),
                ("source_span", "revision_id", "missing-revision"),
                ("app_planning_event", "workflow_id", "missing-workflow"),
                ("app_planning_attempt", "job_id", "missing-job"),
                ("app_reference_section", "document_id", "missing-reference"),
                ("entity", "project_id", self.other_project),
                ("app_ai_job", "branch_id", ALTERNATE_BRANCH),
                ("app_agent_session", "chapter_id", "missing-chapter"),
                ("app_agent_checkpoint", "session_id", "missing-session"),
                ("app_agent_write", "session_id", "missing-session"),
                ("app_agent_checkpoint", "branch_id", ALTERNATE_BRANCH),
                ("app_agent_write", "checkpoint_key", "missing-checkpoint"),
                ("app_agent_write", "checkpoint_ns", "another-namespace"),
                ("ai_run", "project_id", self.other_project)):
            payload = copy.deepcopy(self.payload)
            payload["tables"][table][0][field] = value
            cases.append((table + "." + field, payload))
        payload = copy.deepcopy(self.payload)
        payload["tables"]["branch"] = []
        cases.append(("missing_all_branches", payload))
        for name, payload in cases:
            with self.subTest(graph_issue=name):
                self.assert_bad_preview(make_zip(payload))

    def test_body_source_blob_reference_quote_and_range_hashes(self):
        cases = []
        for table, field, value in (
                ("document_revision", "content_hash", "1" * 64),
                ("app_manuscript_import", "source_sha256", "1" * 64),
                ("app_reference_document", "source_sha256", "1" * 64),
                ("app_reference_document", "text_sha256", "1" * 64),
                ("source_span", "quote_hash", "1" * 64),
                ("source_span", "start_codepoint", -1),
                ("app_reference_section", "body", "forged reference body"),
                ("app_manuscript_import", "source_bytes", {"$blob": "@@@"})):
            payload = copy.deepcopy(self.payload)
            payload["tables"][table][0][field] = value
            cases.append((table + "." + field, payload))
        payload = copy.deepcopy(self.payload)
        sources = [row for row in payload["tables"]["app_ai_job"]
                   if row["id"] == "portable-job-success"]
        self.assertEqual(len(sources), 1)
        source = sources[0]
        context = json.loads(source["context_json"])
        self.assertEqual(len(context["reference_passages"]), 1)
        context["reference_passages"][0]["source_quote"] = "wrong original quote"
        source["context_json"] = encode(context).decode("utf-8")
        cases.append(("job.reference_quote", payload))
        for name, payload in cases:
            with self.subTest(content_issue=name):
                self.assert_bad_preview(make_zip(payload))

    def test_known_additive_old_tables_can_restore_without_guessing(self):
        payload = copy.deepcopy(self.payload)
        payload["tables"].pop("app_entity_scope")
        payload["tables"].pop("app_candidate_revision")
        for table in AGENT_TABLES | CHAT_TABLES:
            payload["tables"].pop(table)
        self.assert_bad_preview(make_zip(payload))
        for row in payload["tables"]["app_ai_job"]:
            for field in ("context_json", "result_json"):
                if row[field]:
                    value = json.loads(row[field])
                    value.pop("_candidate_revision", None)
                    value.pop("candidate_revision", None)
                    value.pop("_agent_chat", None)
                    row[field] = encode(value).decode("utf-8")
        for row in payload["tables"]["ai_run"]:
            value = json.loads(row["consent_json"])
            value.pop("candidate_revision", None)
            row["consent_json"] = encode(value).decode("utf-8")
        preview = self.preview(make_zip(payload))
        self.assertEqual(preview["compatibility"]["absent_known_additive_tables"],
                         sorted(AGENT_TABLES | CHAT_TABLES
                                | {"app_candidate_revision", "app_entity_scope"}))
        status, restored = self.call(
            "POST", "/api/backups/import/apply", self.confirmation(preview))
        self.assertEqual(status, 201, restored)
        self.assertEqual(len(restored["row_counts"]), 52)
        self.assert_complete_restore(restored, preview, payload=payload)
        self.assert_existing_unchanged()

    def test_restored_agent_http_is_readable_but_cannot_resume(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        mapped = restored["identity_map"]["portable-agent-session"]
        before = freeze_database(self.path)
        status, response = self.call("GET", "/api/writing-agent", project=project, branch=branch)
        self.assertEqual(status, 200, response)
        session = next(item for item in response["sessions"] if item["id"] == mapped)
        self.assertEqual(session["status"], "restored")
        self.assertTrue(session["restored_read_only"])
        self.assertEqual(session["checkpoint_id"], "")
        self.assertIsNone(session["current_job"])
        self.assert_zero_write(before)
        for action in ("authorize", "advance", "applied", "cancel"):
            with self.subTest(action=action):
                status, response = self.call(
                    "POST", "/api/writing-agent/" + mapped + "/resume",
                    {"action": action, "checkpoint_id": "portable-agent-latest"},
                    project=project, branch=branch)
                self.assertEqual(status, 400, response)
                self.assertIn("仅供回看", response["error"])
                self.assert_zero_write(before)

    def test_restored_old_model_interfaces_are_read_only_zero_calls(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        mapping = restored["identity_map"]
        self.http.control("synthetic_environment")
        before = freeze_database(self.path)
        status, queue = self.call(
            "POST", "/api/ai/queue/drain", {"limit": 20}, project=project, branch=branch)
        self.assertEqual(status, 200, queue)
        self.assertEqual(queue["processed"], 0)
        self.assert_zero_write(before)
        for job in self.payload["tables"]["app_ai_job"]:
            job_id, run_id = mapping[job["id"]], mapping[job["ai_run_id"]]
            for action in ("process", "retry"):
                with self.subTest(job=job["id"], action=action):
                    status, result = self.call(
                        "POST", "/api/ai/jobs/" + job_id + "/" + action,
                        {"confirm": True}, project=project, branch=branch)
                    if action == "process":
                        self.assertEqual(status, 200, result)
                        self.assertTrue(result["restored_read_only"])
                    else:
                        self.assertIn(status, (400, 409), result)
                    self.assert_zero_write(before)
            for action, request in (
                    ("retry", {"confirm": True}),
                    ("revise", {"confirm": True, "instruction": "整理旧候选",
                                "candidate_hash": digest(self.identities["chapter_id"])}),
                    ("apply", {"confirm": True, "decision": "accepted"})):
                with self.subTest(run=job["ai_run_id"], action=action):
                    status, result = self.call(
                        "POST", "/api/ai/runs/" + run_id + "/" + action,
                        request, project=project, branch=branch)
                    self.assertIn(status, (400, 409), result)
                    self.assert_zero_write(before)
            for decision in ("accepted", "rejected", "edited", "undone"):
                with self.subTest(run=job["ai_run_id"], action="decision", decision=decision):
                    status, result = self.call(
                        "POST", "/api/ai/decision", {
                            "ai_run_id": run_id, "decision": decision, "reason": "旧候选只读"},
                        project=project, branch=branch)
                    self.assertIn(status, (400, 409), result)
                    self.assert_zero_write(before)
        self.assertEqual(self.http.control("status")["calls"], {"provider": 0, "worker": 0})

    def test_new_candidate_decisions_remain_available_without_dispatch(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        chapter = restored["identity_map"][self.identities["chapter_id"]]
        old_records = freeze_database(self.path)
        for decision, compact in (("accepted", "accepted"), ("rejected", "rejected"),
                                  ("edited", "partial"), ("undone", "rejected")):
            with self.subTest(decision=decision):
                status, queued = self.call("POST", "/api/ai/queue", {
                    "task": "continue", "chapter_id": chapter, "defer": True,
                    "instruction": "合成测试，只建立新的独立任务，不调用模型。",
                    "max_tokens": 100,
                }, project=project, branch=branch)
                self.assertEqual(status, 202, queued)
                self.assertFalse(queued["auto_started"])
                status, new_job = self.call("GET", "/api/ai/jobs/" + queued["id"],
                                            project=project, branch=branch)
                self.assertEqual(status, 200, new_job)
                self.assertFalse(new_job["restored_read_only"])
                text = "合成的新候选正文，供作者决定测试使用。"
                with sqlite3.connect(self.path) as conn:
                    context = json.loads(conn.execute(
                        "SELECT context_json FROM app_ai_job WHERE id=?", (queued["id"],)
                    ).fetchone()[0])
                    consent = json.loads(conn.execute(
                        "SELECT consent_json FROM ai_run WHERE id=?", (queued["ai_run_id"],)
                    ).fetchone()[0])
                    self.assertFalse(context.get("_restored_read_only", False))
                    self.assertFalse(consent.get("restored_read_only", False))
                    conn.execute(
                        "UPDATE ai_run SET status='success',output_hash=?,ended_at=? WHERE id=?",
                        (digest(text), "2026-10-15T00:00:00+00:00", queued["ai_run_id"]))
                    conn.execute(
                        "UPDATE app_ai_job SET status='success',result_json=?,finished_at=? WHERE id=?",
                        (encode({"text": text, "billing_state": "synthetic_not_dispatched"}).decode(),
                         "2026-10-15T00:00:00+00:00", queued["id"]))
                status, result = self.call("POST", "/api/ai/decision", {
                    "ai_run_id": queued["ai_run_id"], "decision": decision,
                    "reason": "本次新候选的作者决定。",
                }, project=project, branch=branch)
                self.assertEqual(status, 201, result)
                self.assertEqual(result["decision"], decision)
                self.assertEqual(result["author_decision"], compact)
                with sqlite3.connect(self.path) as conn:
                    saved = conn.execute(
                        "SELECT decision,output_hash,reason FROM app_ai_run_decision WHERE ai_run_id=?",
                        (queued["ai_run_id"],)).fetchall()
                    self.assertEqual(saved, [(decision, digest(text), "本次新候选的作者决定。")])
                    self.assertEqual(conn.execute(
                        "SELECT COUNT(*) FROM event_log WHERE aggregate_id=? "
                        "AND event_type='ai_run.decision'", (queued["ai_run_id"],)).fetchone()[0], 1)
        after = freeze_database(self.path)
        for table in ("ai_run", "app_ai_job", "app_ai_run_decision"):
            self.assertEqual(after["rows"][table][:len(old_records["rows"][table])],
                             old_records["rows"][table], table)
        self.assert_existing_unchanged()
        REPORT["observations"].append({
            "test": self._testMethodName,
            "new_jobs_created_through_http": 4,
            "new_success_results_injected_synthetically": 4,
            "new_author_decisions_saved": 4,
            "old_model_records_unchanged": True,
        })

    def test_each_restored_read_only_marker_independently_blocks_decisions(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        source_job = next(row for row in self.payload["tables"]["app_ai_job"]
                          if row["status"] == "success")
        run_id = restored["identity_map"][source_job["ai_run_id"]]
        job_id = restored["identity_map"][source_job["id"]]
        with sqlite3.connect(self.path) as conn:
            original_context = json.loads(conn.execute(
                "SELECT context_json FROM app_ai_job WHERE id=?", (job_id,)).fetchone()[0])
            original_consent = json.loads(conn.execute(
                "SELECT consent_json FROM ai_run WHERE id=?", (run_id,)).fetchone()[0])
        self.assertTrue(original_context["_restored_read_only"])
        self.assertTrue(original_consent["restored_read_only"])
        for marker in ("run_only", "job_only"):
            context, consent = copy.deepcopy(original_context), copy.deepcopy(original_consent)
            if marker == "run_only":
                context.pop("_restored_read_only")
            else:
                consent.pop("restored_read_only")
            with sqlite3.connect(self.path) as conn:
                conn.execute("UPDATE app_ai_job SET context_json=? WHERE id=?",
                             (encode(context).decode("utf-8"), job_id))
                conn.execute("UPDATE ai_run SET consent_json=? WHERE id=?",
                             (encode(consent).decode("utf-8"), run_id))
            before = freeze_database(self.path)
            for decision in ("accepted", "rejected", "edited", "undone"):
                with self.subTest(surviving_marker=marker, decision=decision):
                    status, result = self.call("POST", "/api/ai/decision", {
                        "ai_run_id": run_id, "decision": decision, "reason": "只读门独立校验",
                    }, project=project, branch=branch)
                    self.assertEqual(status, 400, result)
                    self.assertIn("历史候选只供阅读", result["error"])
                    self.assert_zero_write(before)
        self.assert_existing_unchanged()

    def test_restored_body_edit_and_reference_selection_for_new_task(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        mapping = restored["identity_map"]
        chapter = mapping[self.identities["chapter_id"]]
        before = freeze_database(self.path)
        old_revisions = before["rows"]["document_revision"]
        new_body = self.identities["body"] + "\n恢复副本中新增的一段正文🙂。"
        status, updated = self.call(
            "PATCH", "/api/chapters/" + chapter, {"body": new_body},
            project=project, branch=branch)
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["body"], new_body)
        self.assertNotEqual(updated["revision_id"], mapping[self.identities["latest_revision_id"]])
        after = freeze_database(self.path)
        self.assertEqual(after["rows"]["document_revision"][:len(old_revisions)], old_revisions)
        self.assertEqual(len(after["rows"]["document_revision"]), len(old_revisions) + 1)
        status, reopened = self.call("GET", "/api/state", project=project, branch=branch)
        self.assertEqual(status, 200, reopened)
        self.assertEqual(next(item["body"] for item in reopened["chapters"]
                              if item["id"] == chapter), new_body)
        self.assert_zero_write(after)
        reference_id = mapping[self.identities["reference_id"]]
        route = ("/api/references/" + reference_id + "/search?" +
                 urllib.parse.urlencode({"q": self.identities["chapter_id"]}))
        status, searched = self.call("GET", route, project=project, branch=branch)
        self.assertEqual(status, 200, searched)
        self.assertTrue(searched["results"], searched)
        passage = next(item for item in searched["results"]
                       if item["source_quote"] == self.identities["chapter_id"])
        self.assertEqual(passage["document_title"], self.identities["chapter_id"])
        self.assertEqual(passage["source_document_id"], reference_id)
        self.assertEqual(passage["use"], "reference_not_story_fact")
        selection = {
            "document_id": passage["source_document_id"],
            "section_id": passage["source_section_id"],
            **{name: passage[name] for name in (
                "section_start", "section_end", "source_sha256", "section_hash", "source_hash")},
        }
        self.assert_zero_write(after)
        status, queued = self.call("POST", "/api/ai/queue", {
            "task": "continue", "chapter_id": chapter, "defer": True,
            "instruction": "只用恢复后的参考原句作写法参考。",
            "max_tokens": 100, "reference_selections": [selection],
        }, project=project, branch=branch)
        self.assertEqual(status, 202, queued)
        self.assertFalse(queued["auto_started"])
        status, new_job = self.call("GET", "/api/ai/jobs/" + queued["id"],
                                    project=project, branch=branch)
        self.assertEqual(status, 200, new_job)
        self.assertFalse(new_job["restored_read_only"])
        with sqlite3.connect(self.path) as conn:
            context = json.loads(conn.execute(
                "SELECT context_json FROM app_ai_job WHERE id=?", (queued["id"],)
            ).fetchone()[0])
            consent = json.loads(conn.execute(
                "SELECT consent_json FROM ai_run WHERE id=?", (queued["ai_run_id"],)
            ).fetchone()[0])
            self.assertFalse(context.get("_restored_read_only", False))
            self.assertFalse(consent.get("restored_read_only", False))
            self.assertEqual(context["current_chapter"]["body"], new_body)
            self.assertEqual(len(context["reference_passages"]), 1)
            selected = context["reference_passages"][0]
            self.assertEqual(selected["source_quote"], self.identities["chapter_id"])
            self.assertEqual(selected["document_id"], reference_id)
            self.assertEqual(selected["section_id"], mapping[self.identities["reference_section_id"]])
            self.assertEqual(selected["use"], "narrative_technique_only_not_story_fact")
        status, canceled = self.call(
            "POST", "/api/ai/jobs/" + queued["id"] + "/cancel", {},
            project=project, branch=branch)
        self.assertEqual(status, 200, canceled)
        self.assertEqual(canceled["status"], "canceled")
        self.assert_existing_unchanged()
        REPORT["observations"].append({
            "test": self._testMethodName,
            "new_body_persisted_and_reopened": True, "old_revisions_unchanged": True,
            "restored_reference_search_and_exact_selection": True,
            "new_independent_task_queued_and_canceled_without_dispatch": True,
        })

    def test_full_state_body_save_preserves_unchanged_accepted_history(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        mapping = restored["identity_map"]
        chapter_id, memory_id = mapping[self.identities["chapter_id"]], mapping["portable-memory"]
        status, state = self.call("GET", "/api/state", project=project, branch=branch)
        self.assertEqual(status, 200, state)
        memory = next(item for item in state["memory"] if item["id"] == memory_id)
        self.assertEqual(memory["status"], "已确认")
        self.assertEqual(memory["source_span_status"], "valid")
        self.assertEqual(memory["source_revision_id"], memory["current_revision_id"])
        self.assertFalse(memory["stale"])
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            previous = dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id=?", (memory_id,)).fetchone())
            entity = dict(conn.execute(
                "SELECT * FROM entity WHERE id=?", (previous["entity_id"],)).fetchone())
        self.assertNotEqual(memory["name"], entity["canonical_name"])
        before = freeze_database(self.path)
        request = copy.deepcopy(state)
        request["full_state"] = True
        chapter = next(item for item in request["chapters"] if item["id"] == chapter_id)
        chapter["body"] += "\n电脑编辑器完整保存时增加的新正文🙂。"
        status, saved = self.call(
            "PUT", "/api/state", request, project=project, branch=branch)
        self.assertEqual(status, 200, saved)
        new_chapter = next(item for item in saved["chapters"] if item["id"] == chapter_id)
        self.assertEqual(new_chapter["body"], chapter["body"])
        self.assertNotEqual(new_chapter["revision_id"], chapter["revision_id"])
        saved_memory = next(item for item in saved["memory"] if item["id"] == memory_id)
        self.assertEqual(saved_memory["status"], "已确认")
        self.assertEqual(saved_memory["name"], memory["name"])
        self.assertEqual(saved_memory["confidence_score"], memory["confidence_score"])
        self.assertEqual(saved_memory["source_revision_id"], new_chapter["revision_id"])
        self.assertEqual(saved_memory["current_revision_id"], new_chapter["revision_id"])
        self.assertFalse(saved_memory["stale"])
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            after_memory = dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id=?", (memory_id,)).fetchone())
            for field in ("entity_id", "kind", "status", "confidence", "created_at",
                          "reviewed_at", "reviewed_by", "created_by"):
                self.assertEqual(after_memory[field], previous[field], field)
            before_payload, after_payload = (
                json.loads(previous["payload_json"]), json.loads(after_memory["payload_json"]))
            self.assertEqual(after_payload, before_payload)
            self.assertNotEqual(after_memory["source_span_id"], previous["source_span_id"])
            old_span = conn.execute(
                "SELECT status FROM source_span WHERE id=?", (previous["source_span_id"],)
            ).fetchone()
            self.assertEqual(old_span["status"], "stale")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE aggregate_id=? "
                "AND event_type LIKE 'memory.%'", (memory_id,)).fetchone()[0], 0)
        after = freeze_database(self.path)
        self.assertEqual(after["rows"]["document_revision"][:len(before["rows"]["document_revision"])],
                         before["rows"]["document_revision"])
        status, reopened = self.call("GET", "/api/state", project=project, branch=branch)
        self.assertEqual(status, 200, reopened)
        self.assertEqual(next(item["body"] for item in reopened["chapters"]
                              if item["id"] == chapter_id), chapter["body"])
        self.assert_zero_write(after)
        status, repeated = self.call(
            "PUT", "/api/state", {**reopened, "full_state": True},
            project=project, branch=branch)
        self.assertEqual(status, 200, repeated)
        repeated_snapshot = freeze_database(self.path)
        for table in ("candidate_memory", "source_span", "assertion"):
            self.assertEqual(repeated_snapshot["rows"][table], after["rows"][table], table)
        self.assert_existing_unchanged()
        REPORT["observations"].append({
            "test": self._testMethodName,
            "real_full_state_put_body_save": True,
            "historical_memory_name_differs_from_linked_entity": True,
            "author_review_metadata_and_confidence_preserved": True,
            "chapter_source_reanchoring_still_applied": True,
            "unchanged_memory_not_reconfirmed": True,
        })

    def test_full_state_body_save_keeps_preexisting_stale_accepted_history(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        mapping = restored["identity_map"]
        chapter_id, memory_id = mapping[self.identities["chapter_id"]], mapping["portable-memory"]
        stale_span_id = mapping["portable-span"]
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            stale_span = dict(conn.execute(
                "SELECT * FROM source_span WHERE id=?", (stale_span_id,)).fetchone())
            self.assertEqual(stale_span["status"], "stale")
            self.assertEqual(stale_span["revision_id"], mapping[self.identities["revision_id"]])
            conn.execute("UPDATE candidate_memory SET source_span_id=? WHERE id=?",
                         (stale_span_id, memory_id))
            previous = dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id=?", (memory_id,)).fetchone())
        status, state = self.call("GET", "/api/state", project=project, branch=branch)
        self.assertEqual(status, 200, state)
        memory = next(item for item in state["memory"] if item["id"] == memory_id)
        self.assertEqual(memory["status"], "已确认")
        self.assertTrue(memory["stale"])
        self.assertNotEqual(memory["source_revision_id"], memory["current_revision_id"])
        self.assertEqual(memory["source_span_id"], stale_span_id)
        before = freeze_database(self.path)
        request = copy.deepcopy(state)
        request["full_state"] = True
        chapter = next(item for item in request["chapters"] if item["id"] == chapter_id)
        chapter["body"] += "\n保留既有失效证据时新增的正文🙂。"
        status, saved = self.call(
            "PUT", "/api/state", request, project=project, branch=branch)
        self.assertEqual(status, 200, saved)
        new_chapter = next(item for item in saved["chapters"] if item["id"] == chapter_id)
        self.assertEqual(new_chapter["body"], chapter["body"])
        self.assertNotEqual(new_chapter["revision_id"], chapter["revision_id"])
        saved_memory = next(item for item in saved["memory"] if item["id"] == memory_id)
        for field in ("name", "status", "confidence_score", "source_span_id",
                      "source_revision_id", "approved_at", "reviewed_by", "created_at"):
            self.assertEqual(saved_memory[field], memory[field], field)
        self.assertTrue(saved_memory["stale"])
        self.assertEqual(saved_memory["source_span_status"], "stale")
        self.assertEqual(saved_memory["current_revision_id"], new_chapter["revision_id"])
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            after_memory = dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id=?", (memory_id,)).fetchone())
            self.assertEqual(after_memory, previous)
            self.assertEqual(dict(conn.execute(
                "SELECT * FROM source_span WHERE id=?", (stale_span_id,)).fetchone()), stale_span)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE aggregate_id=? "
                "AND event_type LIKE 'memory.%'", (memory_id,)).fetchone()[0], 0)
        after = freeze_database(self.path)
        self.assertEqual(after["rows"]["document_revision"][:len(before["rows"]["document_revision"])],
                         before["rows"]["document_revision"])
        status, reopened = self.call("GET", "/api/state", project=project, branch=branch)
        self.assertEqual(status, 200, reopened)
        self.assert_zero_write(after)
        status, repeated = self.call(
            "PUT", "/api/state", {**reopened, "full_state": True},
            project=project, branch=branch)
        self.assertEqual(status, 200, repeated)
        repeated_snapshot = freeze_database(self.path)
        for table in ("candidate_memory", "source_span", "assertion"):
            self.assertEqual(repeated_snapshot["rows"][table], after["rows"][table], table)
        self.assert_existing_unchanged()
        REPORT["observations"].append({
            "test": self._testMethodName,
            "preexisting_stale_history_injected_synthetically": True,
            "old_span_and_candidate_memory_entire_row_preserved": True,
            "payload_and_author_review_timestamp_not_rewritten": True,
            "stale_revision_not_promoted_by_unchanged_full_state_save": True,
        })

    def test_full_state_body_save_invalidates_removed_current_evidence(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        mapping = restored["identity_map"]
        chapter_id, memory_id = mapping[self.identities["chapter_id"]], mapping["portable-memory"]
        status, state = self.call("GET", "/api/state", project=project, branch=branch)
        self.assertEqual(status, 200, state)
        memory = next(item for item in state["memory"] if item["id"] == memory_id)
        self.assertEqual(memory["status"], "已确认")
        self.assertEqual(memory["source_span_status"], "valid")
        self.assertFalse(memory["stale"])
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            previous = dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id=?", (memory_id,)).fetchone())
        request = copy.deepcopy(state)
        request["full_state"] = True
        chapter = next(item for item in request["chapters"] if item["id"] == chapter_id)
        self.assertEqual(chapter["body"].count(memory["source_quote"]), 1)
        chapter["body"] = chapter["body"].replace(
            memory["source_quote"], "正文中已移除这条证据。", 1)
        status, saved = self.call(
            "PUT", "/api/state", request, project=project, branch=branch)
        self.assertEqual(status, 200, saved)
        new_chapter = next(item for item in saved["chapters"] if item["id"] == chapter_id)
        self.assertEqual(new_chapter["body"], chapter["body"])
        self.assertNotEqual(new_chapter["revision_id"], chapter["revision_id"])
        saved_memory = next(item for item in saved["memory"] if item["id"] == memory_id)
        self.assertEqual(saved_memory["status"], "stale")
        self.assertTrue(saved_memory["stale"])
        self.assertEqual(saved_memory["source_span_id"], memory["source_span_id"])
        self.assertEqual(saved_memory["source_revision_id"], memory["source_revision_id"])
        self.assertNotEqual(saved_memory["source_revision_id"], saved_memory["current_revision_id"])
        self.assertEqual(saved_memory["source_span_status"], "stale")
        self.assertEqual(saved_memory["confidence_score"], memory["confidence_score"])
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            after_memory = dict(conn.execute(
                "SELECT * FROM candidate_memory WHERE id=?", (memory_id,)).fetchone())
            self.assertEqual(after_memory, {**previous, "status": "stale"})
            assertion = conn.execute(
                "SELECT state FROM assertion WHERE id=?", (mapping["portable-assertion"],)
            ).fetchone()
            self.assertEqual(assertion["state"], "candidate")
        after = freeze_database(self.path)
        status, reopened = self.call("GET", "/api/state", project=project, branch=branch)
        self.assertEqual(status, 200, reopened)
        self.assert_zero_write(after)
        status, repeated = self.call(
            "PUT", "/api/state", {**reopened, "full_state": True},
            project=project, branch=branch)
        self.assertEqual(status, 200, repeated)
        repeated_snapshot = freeze_database(self.path)
        for table in ("candidate_memory", "source_span", "assertion"):
            self.assertEqual(repeated_snapshot["rows"][table], after["rows"][table], table)
        self.assert_existing_unchanged()
        REPORT["observations"].append({
            "test": self._testMethodName,
            "current_valid_source_quote_removed_by_real_full_state_put": True,
            "memory_stale_and_canon_assertion_returned_to_candidate": True,
            "repeat_save_does_not_reconfirm_invalidated_history": True,
        })

    def test_full_state_tampered_or_new_unproven_accepted_memory_rolls_back(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        mapping = restored["identity_map"]
        status, state = self.call("GET", "/api/state", project=project, branch=branch)
        self.assertEqual(status, 200, state)
        memory_id = mapping["portable-memory"]
        original_memory = next(item for item in state["memory"] if item["id"] == memory_id)
        with sqlite3.connect(self.path) as conn:
            entity = conn.execute(
                "SELECT kind,canonical_name FROM entity WHERE id=?",
                (mapping[self.identities["entity_id"]],)).fetchone()
        before = freeze_database(self.path)
        for issue in ("changed_name", "changed_quote", "new_unproven_accepted"):
            with self.subTest(accepted_memory_issue=issue):
                request = copy.deepcopy(state)
                request["full_state"] = True
                chapter = next(item for item in request["chapters"]
                               if item["id"] == mapping[self.identities["chapter_id"]])
                chapter["body"] += "\n这段正文随坏记忆请求一起回滚。"
                memory = next(item for item in request["memory"] if item["id"] == memory_id)
                if issue == "changed_name":
                    memory["name"] = "并不存在的伪造人物"
                elif issue == "changed_quote":
                    memory["source_quote"] = "正文里不存在的伪造原句"
                    memory["source_hash"] = digest(memory["source_quote"])
                else:
                    unproven = copy.deepcopy(original_memory)
                    unproven.update(
                        id="portable-http-unproven-memory", key="portable-http-unproven-memory",
                        name=entity[1], type=entity[0], status="已确认",
                        source_chapter_id=None, source_quote="", source_start=None, source_end=None,
                        source_hash="", source_revision_id=None, current_revision_id=None,
                        confidence_score=1,
                    )
                    # A pasted span ID without current quote evidence must not grant confirmation.
                    unproven["source_span_id"] = original_memory["source_span_id"]
                    request["memory"].append(unproven)
                status, rejected = self.call(
                    "PUT", "/api/state", request, project=project, branch=branch)
                self.assertEqual(status, 400, rejected)
                self.assertIn("accepted memory", rejected["error"])
                self.assert_zero_write(before)

    def test_full_state_unchanged_memory_cannot_skip_inactive_or_changed_entity(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        mapping = restored["identity_map"]
        entity_id = mapping[self.identities["entity_id"]]
        with sqlite3.connect(self.path) as conn:
            original = conn.execute(
                "SELECT status,kind FROM entity WHERE id=?", (entity_id,)).fetchone()
        for issue in ("archived", "changed_kind"):
            with self.subTest(linked_entity_issue=issue):
                with sqlite3.connect(self.path) as conn:
                    conn.execute("UPDATE entity SET status=?,kind=? WHERE id=?",
                                 ("archived" if issue == "archived" else original[0],
                                  "地点" if issue == "changed_kind" else original[1], entity_id))
                status, state = self.call("GET", "/api/state", project=project, branch=branch)
                self.assertEqual(status, 200, state)
                before = freeze_database(self.path)
                request = copy.deepcopy(state)
                request["full_state"] = True
                chapter = next(item for item in request["chapters"]
                               if item["id"] == mapping[self.identities["chapter_id"]])
                chapter["body"] += "\n人物归属失效时这段正文必须回滚。"
                status, rejected = self.call(
                    "PUT", "/api/state", request, project=project, branch=branch)
                self.assertEqual(status, 400, rejected)
                self.assertIn("accepted memory", rejected["error"])
                self.assert_zero_write(before)
                with sqlite3.connect(self.path) as conn:
                    conn.execute("UPDATE entity SET status=?,kind=? WHERE id=?",
                                 (*original, entity_id))
        self.assert_existing_unchanged()

    def test_restored_planning_cannot_start_confirm_or_apply_again(self):
        restored, unused = self.restore()
        project, branch = restored["project_id"], restored["branch_id"]
        before = freeze_database(self.path)
        for old in self.payload["tables"]["app_planning_workflow"]:
            workflow_id = restored["identity_map"][old["id"]]
            route = "/api/planning-workflows/" + workflow_id
            status, workflow = self.call("GET", route, project=project, branch=branch)
            self.assertEqual(status, 200, workflow)
            self.assertEqual(workflow["status"], "canceled")
            request = {
                "expected_stage": workflow["stage"], "expected_version": workflow["version"],
                "expected_source_version": workflow["source_version"],
                "attempt_id": (workflow.get("attempt") or {}).get("id"),
                "candidate_hash": (workflow.get("attempt") or {}).get("candidate_hash"),
                "confirm": True, "max_tokens": 100,
            }
            for action in ("start", "confirm", "apply"):
                with self.subTest(workflow=old["id"], action=action):
                    status, result = self.call(
                        "POST", route + "/" + action, request, project=project, branch=branch)
                    if old["id"] == "portable-completed-workflow" and action == "apply":
                        self.assertEqual(status, 200, result)
                        self.assertTrue(result["idempotent"])
                    else:
                        self.assertIn(status, (400, 409), result)
                    self.assert_zero_write(before)

    def test_inline_external_settings_secrets_are_removed_everywhere(self):
        payload = copy.deepcopy(self.payload)
        row = payload["tables"]["app_model_settings"][0]
        settings = json.loads(row["settings_json"])
        settings.update(api_key=SYNTHETIC_KEY, token=SYNTHETIC_KEY,
                        credentials={"api_key": SYNTHETIC_KEY},
                        _credential_ref=SYNTHETIC_KEY, last_probe={"raw": SYNTHETIC_KEY})
        settings["parameters"]["api_key"] = SYNTHETIC_KEY
        settings["styles"]["api_key"] = SYNTHETIC_KEY
        settings["models"]["unknown_task"] = SYNTHETIC_KEY
        row["settings_json"] = encode(settings).decode("utf-8")
        restored, preview = self.restore(make_zip(payload))
        self.assertNotIn(SYNTHETIC_KEY, encode(preview).decode("utf-8"))
        self.assertNotIn(SYNTHETIC_KEY, encode(restored["state"]).decode("utf-8"))
        project, branch = restored["project_id"], restored["branch_id"]
        status, settings = self.call("GET", "/api/ai/settings", project=project, branch=branch)
        self.assertEqual(status, 200, settings)
        self.assertNotIn(SYNTHETIC_KEY, encode(settings).decode("utf-8"))
        status, backup = self.call(
            "POST", "/api/backups", {}, project=project, branch=branch)
        self.assertEqual(status, 201, backup)
        status, raw = self.call(
            "GET", "/api/backups/" + backup["id"] + "/download",
            project=project, branch=branch, binary=True)
        self.assertEqual(status, 200)
        archived, manifest, raw_project = decode_zip(raw)
        self.assertNotIn(SYNTHETIC_KEY.encode("utf-8"), raw_project)
        self.assertEqual(list(self.credentials.iterdir()), [])
        self.assert_existing_unchanged()

    def test_restored_book_can_be_backed_up_downloaded_and_restored_again(self):
        first, unused = self.restore()
        project, branch = first["project_id"], first["branch_id"]
        status, backup = self.call(
            "POST", "/api/backups", {}, project=project, branch=branch)
        self.assertEqual(status, 201, backup)
        status, raw = self.call(
            "GET", "/api/backups/" + backup["id"] + "/download",
            project=project, branch=branch, binary=True)
        self.assertEqual(status, 200, raw)
        payload, unused_manifest, unused_project = decode_zip(raw)
        before = freeze_database(self.path)
        second, preview = self.restore(raw, title="第二次独立恢复副本")
        self.assertNotEqual(second["project_id"], first["project_id"])
        self.assert_existing_unchanged(before)
        self.assert_complete_restore(
            second, preview, payload=payload, title="第二次独立恢复副本")


class EvidenceResult(unittest.TextTestResult):
    def addSuccess(self, test):
        REPORT["cases"].append({"test": test._testMethodName, "kind": "method", "passed": True})
        super().addSuccess(test)

    def addFailure(self, test, error):
        REPORT["cases"].append({
            "test": getattr(test, "_testMethodName", str(test)), "kind": "method",
            "passed": False, "failure": public_value(self._exc_info_to_string(error, test)),
        })
        super().addFailure(test, error)

    def addError(self, test, error):
        REPORT["cases"].append({
            "test": getattr(test, "_testMethodName", str(test)), "kind": "method",
            "passed": False, "error": public_value(self._exc_info_to_string(error, test)),
        })
        super().addError(test, error)

    def addSubTest(self, test, subtest, error):
        REPORT["cases"].append({
            "test": test._testMethodName, "kind": "subtest",
            "parameters": public_value(dict(subtest.params)), "passed": error is None,
            **({"failure": public_value(self._exc_info_to_string(error, test))}
               if error is not None else {}),
        })
        super().addSubTest(test, subtest, error)


def main():
    output = os.environ.get("MOYU_PORTABLE_HTTP_REPORT")
    target = Path(output) if output else None
    if target is not None and target.exists():
        raise SystemExit("Refusing to overwrite existing portable HTTP evidence.")
    REPORT["started_at"] = datetime.now(timezone.utc).isoformat()
    REPORT["source_files_sha256_before"] = fingerprints()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PortableBackupHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    REPORT["checks"] = result.testsRun
    REPORT["source_files_sha256_after"] = fingerprints()
    REPORT["source_unchanged"] = (
        REPORT["source_files_sha256_before"] == REPORT["source_files_sha256_after"])
    REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT["passed"] = result.wasSuccessful() and REPORT["source_unchanged"]
    REPORT["case_count_includes_field_subtests"] = True
    REPORT.pop("temporary_root", None)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(json.dumps({name: REPORT[name] for name in (
        "passed", "checks", "source_unchanged", "free_provider_calls",
        "paid_provider_calls", "external_model_calls", "worker_calls",
        "attempted_provider_calls", "attempted_worker_calls")}))
    return 0 if REPORT["passed"] else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
