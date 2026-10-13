"""Durable, four-stage AI story-planning workflows.

The module is intentionally independent from HTTP and provider code.  A caller
supplies callbacks for its existing durable AI queue; this module stores the
workflow state and validates stage candidates without writing manuscript text
or replacing the application's current blueprint.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

from moyu_provider import validate_json
from moyu_workspace import current_scope, validate_blueprint, validate_id


STAGES = ("overview", "volumes", "chapters", "scenes")
ACTIVE_ATTEMPT_STATUSES = ("queued", "running", "complete", "unknown")
TERMINAL_ATTEMPT_STATUSES = ("confirmed", "failed", "canceled")
WORKFLOW_STATUSES = ("active", "completed", "canceled")
DEFAULT_COUNTS = {"volumes": 2, "chapters_per_volume": 2, "scenes_per_chapter": 2}


class PlanningError(ValueError):
    """Base error for malformed or unavailable workflow state."""


class PlanningNotFound(PlanningError):
    status = 404


class PlanningConflict(PlanningError):
    status = 409


class PlanningDispatchUnknown(PlanningError):
    """The create callback may have dispatched before it failed."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise PlanningError(f"{field} must be text of at most {limit} characters")
    return value


def _positive_int(value: Any, field: str, *, default: int | None = None,
                  maximum: int = 200) -> int:
    if value is None and default is not None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PlanningError(f"{field} must be an integer between 1 and {maximum}")
    return value


def _nonnegative_int(value: Any, field: str, *, default: int = 0,
                     maximum: int = 2_000_000_000) -> int:
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise PlanningError(f"{field} must be a non-negative integer")
    return value


def install_schema(conn: sqlite3.Connection) -> None:
    """Install workflow tables and an index that blocks duplicate active jobs."""
    statements = """
        CREATE TABLE IF NOT EXISTS app_planning_workflow(
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          branch_id TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('active','completed','canceled')),
          current_stage TEXT NOT NULL CHECK(current_stage IN ('overview','volumes','chapters','scenes')),
          stage_index INTEGER NOT NULL CHECK(stage_index BETWEEN 0 AND 4),
          version INTEGER NOT NULL DEFAULT 0,
          initial_json TEXT NOT NULL,
          blueprint_json TEXT NOT NULL,
          source_blueprint_json TEXT NOT NULL,
          final_blueprint_json TEXT,
          source_version INTEGER NOT NULL DEFAULT 0,
          source_hash TEXT NOT NULL,
          cancel_reason TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS app_planning_workflow_scope
          ON app_planning_workflow(project_id, branch_id, updated_at);

        CREATE TABLE IF NOT EXISTS app_planning_attempt(
          id TEXT PRIMARY KEY,
          workflow_id TEXT NOT NULL REFERENCES app_planning_workflow(id),
          stage TEXT NOT NULL CHECK(stage IN ('overview','volumes','chapters','scenes')),
          attempt_no INTEGER NOT NULL,
          status TEXT NOT NULL CHECK(status IN
            ('queued','running','complete','unknown','confirmed','failed','canceled')),
          job_id TEXT,
          source_version INTEGER NOT NULL,
          source_hash TEXT NOT NULL,
          candidate_json TEXT,
          candidate_hash TEXT,
          max_tokens INTEGER,
          error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(workflow_id, stage, attempt_no)
        );
        CREATE INDEX IF NOT EXISTS app_planning_attempt_workflow
          ON app_planning_attempt(workflow_id, stage, attempt_no);
        CREATE UNIQUE INDEX IF NOT EXISTS app_planning_one_active_attempt
          ON app_planning_attempt(workflow_id, stage)
          WHERE status IN ('queued','running','complete','unknown');

        CREATE TABLE IF NOT EXISTS app_planning_event(
          id TEXT PRIMARY KEY,
          workflow_id TEXT NOT NULL REFERENCES app_planning_workflow(id),
          attempt_id TEXT,
          event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS app_planning_event_workflow
          ON app_planning_event(workflow_id, id);
        """
    # executescript() would commit a caller's open transaction.
    for statement in statements.split(";"):
        if statement.strip():
            conn.execute(statement)


@contextmanager
def _atomic(conn: sqlite3.Connection):
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    name = "planning_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + name)
    try:
        # Force a writer lock before reading a stage or source version.
        conn.execute("UPDATE app_planning_workflow SET version=version WHERE 0")
        yield
    except BaseException:
        conn.execute("ROLLBACK TO " + name)
        conn.execute("RELEASE " + name)
        raise
    else:
        conn.execute("RELEASE " + name)


def _transactional(function):
    @wraps(function)
    def wrapped(conn, *args, **kwargs):
        with _atomic(conn):
            return function(conn, *args, **kwargs)
    return wrapped


def schema(conn: sqlite3.Connection | None = None) -> dict[str, dict[str, Any]]:
    """Return bounded JSON contracts for the four stage responses."""
    result = {
        "overview": {
            "type": "object",
            "required": ["premise", "genre", "audience", "tone", "contract"],
            "additionalProperties": False,
            "properties": {
                key: {"type": "string", "maxLength": limit}
                for key, limit in (
                    ("premise", 10000), ("genre", 100), ("audience", 1000),
                    ("tone", 3000), ("contract", 20000),
                )
            },
        },
        "volumes": {
            "type": "object",
            "required": ["volumes"],
            "additionalProperties": False,
            "properties": {
                "volumes": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "items": {
                        "type": "object", "required": ["id", "title", "summary"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "maxLength": 128},
                            "title": {"type": "string", "maxLength": 500},
                            "summary": {"type": "string", "maxLength": 20000},
                        },
                    },
                },
            },
        },
        "chapters": {
            "type": "object",
            "required": ["volumes"],
            "additionalProperties": False,
            "properties": {
                "volumes": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "items": {
                        "type": "object", "required": ["id", "chapters"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "maxLength": 128},
                            "chapters": {
                                "type": "array", "minItems": 1, "maxItems": 1000,
                                "items": {
                                    "type": "object",
                                    "required": ["id", "title", "goal", "pov", "hook"],
                                    "additionalProperties": False,
                                    "properties": {
                                        "id": {"type": "string", "maxLength": 128},
                                        "title": {"type": "string", "maxLength": 500},
                                        "goal": {"type": "string", "maxLength": 5000},
                                        "pov": {"type": "string", "maxLength": 500},
                                        "hook": {"type": "string", "maxLength": 5000},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        "scenes": {
            "type": "object",
            "required": ["volumes"],
            "additionalProperties": False,
            "properties": {
                "volumes": {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "items": {
                        "type": "object", "required": ["id", "chapters"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "maxLength": 128},
                            "chapters": {
                                "type": "array", "minItems": 1, "maxItems": 1000,
                                "items": {
                                    "type": "object",
                                    "required": ["id", "scenes"],
                                    "additionalProperties": False,
                                    "properties": {
                                        "id": {"type": "string", "maxLength": 128},
                                        "scenes": {
                                            "type": "array", "minItems": 1, "maxItems": 200,
                                            "items": {
                                                "type": "object",
                                                "required": ["id", "title", "goal", "location", "pov"],
                                                "additionalProperties": False,
                                                "properties": {
                                                    "id": {"type": "string", "maxLength": 128},
                                                    "title": {"type": "string", "maxLength": 500},
                                                    "goal": {"type": "string", "maxLength": 5000},
                                                    "location": {"type": "string", "maxLength": 500},
                                                    "pov": {"type": "string", "maxLength": 500},
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }
    return copy.deepcopy(result)


def _normal_counts(initial: Mapping[str, Any], options: Mapping[str, Any] | None) -> dict[str, int]:
    options = options if isinstance(options, Mapping) else {}
    counts = initial.get("counts") if isinstance(initial.get("counts"), Mapping) else {}
    merged = {**DEFAULT_COUNTS, **dict(counts), **{
        key: options[key] for key in DEFAULT_COUNTS if key in options
    }}
    result = {
        "volumes": _positive_int(merged.get("volumes"), "counts.volumes", maximum=200),
        "chapters_per_volume": _positive_int(
            merged.get("chapters_per_volume"), "counts.chapters_per_volume", maximum=1000
        ),
        "scenes_per_chapter": _positive_int(
            merged.get("scenes_per_chapter"), "counts.scenes_per_chapter", maximum=200
        ),
    }
    if result["volumes"] * result["chapters_per_volume"] > 1000:
        raise PlanningError("a workflow may plan at most 1000 chapters")
    if result["volumes"] * result["chapters_per_volume"] * result["scenes_per_chapter"] > 10000:
        raise PlanningError("a workflow may plan at most 10000 scenes")
    return result


def _skeleton(initial: Mapping[str, Any], counts: Mapping[str, int],
              workflow_id: str) -> dict[str, Any]:
    """Make stable IDs for the initial plan without pretending it is confirmed."""
    namespace = validate_id(workflow_id, "workflow id").removeprefix("planwf_")
    volumes = []
    for volume_index in range(1, counts["volumes"] + 1):
        volume_id = f"vol_{namespace}_{volume_index:03d}"
        chapters = []
        for chapter_index in range(1, counts["chapters_per_volume"] + 1):
            chapter_id = f"plan_{namespace}_{volume_index:03d}_{chapter_index:03d}"
            scenes = [
                {
                    "id": f"scene_{namespace}_{volume_index:03d}_{chapter_index:03d}_{scene_index:03d}",
                    "title": "",
                    "goal": "",
                    "location": "",
                    "pov": "",
                }
                for scene_index in range(1, counts["scenes_per_chapter"] + 1)
            ]
            chapters.append({
                "id": chapter_id, "title": "", "goal": "", "pov": "", "hook": "",
                "scenes": scenes,
            })
        volumes.append({"id": volume_id, "title": "", "summary": "", "chapters": chapters})
    return {
        "premise": _text(initial.get("premise", ""), "premise", 10000),
        "genre": _text(initial.get("genre", ""), "genre", 100),
        "audience": _text(initial.get("audience", ""), "audience", 1000),
        "tone": _text(initial.get("tone", ""), "tone", 3000),
        "contract": _text(initial.get("contract", ""), "contract", 20000),
        "counts": dict(counts),
        "volumes": volumes,
    }


def _row_json(row: Mapping[str, Any], key: str, default: Any) -> Any:
    raw = row[key]
    return default if raw in (None, "") else json.loads(raw)


def _scope_check(row: Mapping[str, Any], project_id: str | None, branch_id: str | None) -> None:
    inherited = current_scope()
    if inherited:
        if row["project_id"] != inherited[0] or row["branch_id"] != inherited[1]:
            raise PlanningNotFound("workflow not found in current project and branch")
    if project_id is not None and row["project_id"] != project_id:
        raise PlanningNotFound("workflow not found in project")
    if branch_id is not None and row["branch_id"] != branch_id:
        raise PlanningNotFound("workflow not found in branch")


def _load(conn: sqlite3.Connection, workflow_id: str, project_id: str | None = None,
          branch_id: str | None = None) -> dict[str, Any]:
    validate_id(workflow_id, "workflow id")
    rows = _rows(conn, "SELECT * FROM app_planning_workflow WHERE id=?", (workflow_id,))
    row = rows[0] if rows else None
    if row is None:
        raise PlanningNotFound("workflow not found")
    _scope_check(row, project_id, branch_id)
    return row


def _rows(conn: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, parameters)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _event(conn: sqlite3.Connection, workflow_id: str, event_type: str,
           payload: Mapping[str, Any], attempt_id: str | None = None,
           callbacks: Mapping[str, Callable[..., Any]] | None = None) -> None:
    conn.execute(
        "INSERT INTO app_planning_event(id,workflow_id,attempt_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)",
        (_id("planevt_"), workflow_id, attempt_id, event_type, _json(dict(payload)), _now()),
    )
    append = (callbacks or {}).get("append_event")
    if append:
        _invoke(append, conn=conn, workflow_id=workflow_id, attempt_id=attempt_id,
                event_type=event_type, payload=dict(payload))


def _invoke(callback: Callable[..., Any], **kwargs: Any) -> Any:
    """Callbacks use keyword arguments and must not commit or dispatch HTTP."""
    return callback(**kwargs)


def _reader(conn: sqlite3.Connection, callbacks: Mapping[str, Callable[..., Any]] | None,
            project_id: str, branch_id: str, fallback_version: int,
            fallback_blueprint: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    reader = (callbacks or {}).get("blueprint_reader")
    if not reader:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_blueprint'").fetchone()
        if not exists:
            return fallback_version, copy.deepcopy(dict(fallback_blueprint))
        row = conn.execute("SELECT version,payload_json FROM app_blueprint WHERE project_id=? AND branch_id=?",
                           (project_id, branch_id)).fetchone()
        if row:
            return row[0], json.loads(row[1])
        return 0, validate_blueprint({})
    raw = _invoke(reader, conn=conn, project_id=project_id, branch_id=branch_id)
    if isinstance(raw, Mapping):
        version = raw.get("version", fallback_version)
        blueprint = raw.get("blueprint", fallback_blueprint)
    elif isinstance(raw, tuple) and len(raw) == 2:
        version, blueprint = raw
    else:
        raise PlanningError("blueprint_reader must return {version, blueprint}")
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise PlanningError("blueprint version must be a non-negative integer")
    if not isinstance(blueprint, Mapping):
        raise PlanningError("blueprint_reader returned an invalid blueprint")
    return version, copy.deepcopy(dict(blueprint))


def _validate_initial(initial: Any) -> dict[str, Any]:
    if not isinstance(initial, Mapping):
        raise PlanningError("initial must be an object")
    return {
        "premise": _text(initial.get("premise", ""), "premise", 10000),
        "genre": _text(initial.get("genre", ""), "genre", 100),
        "audience": _text(initial.get("audience", ""), "audience", 1000),
        "tone": _text(initial.get("tone", ""), "tone", 3000),
        "contract": _text(initial.get("contract", ""), "contract", 20000),
        "counts": _normal_counts(initial, None),
    }


@_transactional
def create_workflow(conn: sqlite3.Connection, project_id: str, branch_id: str,
                    initial: Mapping[str, Any], options: Mapping[str, Any] | None = None
                    ) -> dict[str, Any]:
    """Create an active overview workflow with a stable, unconfirmed skeleton."""
    if options is not None and not isinstance(options, Mapping):
        raise PlanningError("workflow options must be an object")
    validate_id(project_id, "project id")
    validate_id(branch_id, "branch id")
    inherited = current_scope()
    if inherited and (project_id, branch_id) != inherited[:2]:
        raise PlanningNotFound("workflow scope does not match the current project")
    branch_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='branch'").fetchone()
    if branch_table and not conn.execute("SELECT 1 FROM branch WHERE id=? AND project_id=?",
                                         (branch_id, project_id)).fetchone():
        raise PlanningNotFound("branch not found in project")
    initial_values = _validate_initial(initial)
    counts = _normal_counts(initial_values, options)
    initial_values["counts"] = counts
    options = options if isinstance(options, Mapping) else {}
    fallback_version = _nonnegative_int(options.get("source_version"), "source_version")
    source_version, source_blueprint = _reader(
        conn, options.get("callbacks"), project_id, branch_id, fallback_version,
        options.get("source_blueprint") or validate_blueprint({}),
    )
    if "source_version" in options and source_version != options["source_version"]:
        raise PlanningConflict("source blueprint version changed")
    workflow_id = _id("planwf_")
    blueprint = _skeleton(initial_values, counts, workflow_id)
    now = _now()
    conn.execute(
        """
        INSERT INTO app_planning_workflow(
          id,project_id,branch_id,status,current_stage,stage_index,version,
          initial_json,blueprint_json,source_blueprint_json,final_blueprint_json,source_version,source_hash,
          cancel_reason,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            workflow_id, project_id, branch_id, "active", STAGES[0], 0, 0,
            _json(initial_values), _json(blueprint), _json(source_blueprint), None, source_version,
            _hash({"version": source_version, "blueprint": source_blueprint}), None, now, now,
        ),
    )
    _event(conn, workflow_id, "workflow.created", {
        "stage": "overview", "source_version": source_version,
    }, callbacks=options.get("callbacks") if isinstance(options.get("callbacks"), Mapping) else None)
    return get_workflow(conn, workflow_id, project_id, branch_id)


def _attempt_payload(row: Mapping[str, Any], candidate: Any = None) -> dict[str, Any]:
    return {
        "id": row["id"], "workflow_id": row["workflow_id"], "stage": row["stage"],
        "attempt_no": row["attempt_no"], "status": row["status"], "job_id": row["job_id"],
        "source_version": row["source_version"], "source_hash": row["source_hash"],
        "candidate": candidate if candidate is not None else _row_json(row, "candidate_json", None),
        "candidate_hash": row["candidate_hash"], "max_tokens": row["max_tokens"],
        "error": row["error"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def get_workflow(conn: sqlite3.Connection, workflow_id: str,
                 project_id: str | None = None, branch_id: str | None = None,
                 callbacks: Mapping[str, Callable[..., Any]] | None = None) -> dict[str, Any]:
    row = _load(conn, workflow_id, project_id, branch_id)
    attempts = _rows(conn,
        "SELECT * FROM app_planning_attempt WHERE workflow_id=? ORDER BY stage,attempt_no",
        (workflow_id,),
    )
    current = [item for item in attempts if item["stage"] == row["current_stage"]]
    latest = max(current, key=lambda item: item["attempt_no"]) if current else None
    result = {
        "id": row["id"], "workflow_id": row["id"], "project_id": row["project_id"],
        "branch_id": row["branch_id"], "status": row["status"],
        "stage": row["current_stage"], "current_stage": row["current_stage"],
        "stage_index": row["stage_index"], "version": row["version"],
        "source_version": row["source_version"], "source_hash": row["source_hash"],
        "initial": _row_json(row, "initial_json", {}),
        "blueprint": _row_json(row, "blueprint_json", {}),
        "final_blueprint": _row_json(row, "final_blueprint_json", None),
        "attempt": _attempt_payload(latest) if latest else None,
        "attempts": [_attempt_payload(item) for item in attempts],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "cancel_reason": row["cancel_reason"],
    }
    if latest and latest["job_id"] and callbacks and callbacks.get("get_job"):
        # Reading state is intentionally best-effort; it never mutates the
        # database. Call refresh_stage when a durable update is desired.
        try:
            result["job"] = _invoke(callbacks["get_job"], conn=conn, job_id=latest["job_id"],
                                    workflow_id=workflow_id, attempt_id=latest["id"])
        except Exception as exc:  # pragma: no cover - telemetry only
            result["job_read_error"] = str(exc)
    return result


def _latest_attempt(conn: sqlite3.Connection, workflow_id: str, stage: str) -> dict[str, Any] | None:
    rows = _rows(conn,
        "SELECT * FROM app_planning_attempt WHERE workflow_id=? AND stage=? ORDER BY attempt_no DESC LIMIT 1",
        (workflow_id, stage),
    )
    return rows[0] if rows else None


def _stage_input(blueprint: Mapping[str, Any], stage: str) -> dict[str, Any]:
    if stage == "overview":
        return {key: blueprint.get(key, "") for key in ("premise", "genre", "audience", "tone", "contract")}
    if stage == "volumes":
        return {"volumes": [
            {key: volume.get(key, "") for key in ("id", "title", "summary")}
            for volume in blueprint.get("volumes", [])
        ]}
    if stage == "chapters":
        return {"volumes": [
            {"id": volume.get("id"), "chapters": [
                {key: chapter.get(key, "") for key in ("id", "title", "goal", "pov", "hook")}
                for chapter in volume.get("chapters", [])
            ]}
            for volume in blueprint.get("volumes", [])
        ]}
    return {"volumes": [
        {"id": volume.get("id"), "chapters": [
            {"id": chapter.get("id"), "scenes": [
                {key: scene.get(key, "") for key in ("id", "title", "goal", "location", "pov")}
                for scene in chapter.get("scenes", [])
            ]}
            for chapter in volume.get("chapters", [])
        ]}
        for volume in blueprint.get("volumes", [])
    ]}


def _source_payload(row: Mapping[str, Any], stage: str, blueprint: Mapping[str, Any],
                    source_version: int, source_hash: str, max_tokens: int | None) -> dict[str, Any]:
    task_schema = schema()[stage]
    task = "scene" if stage == "scenes" else "outline"
    stage_labels = {"overview": "故事核心与读者承诺", "volumes": "各卷故事走向",
                    "chapters": "各章目标、视角和章节出口", "scenes": "每章各场景的目标与地点"}
    context = {
        "project": {"id": row["project_id"], "genre": blueprint.get("genre", "")},
        "blueprint": copy.deepcopy(dict(blueprint)),
        "planning_workflow": {"id": row["id"], "stage": stage,
                              "source_version": source_version},
        "_task_schema": task_schema,
    }
    return {
        "workflow_id": row["id"], "stage": stage, "project_id": row["project_id"],
        "branch_id": row["branch_id"], "task": task,
        "attempt_id": None, "source_version": source_version, "source_hash": source_hash,
        "schema": task_schema, "input": _stage_input(blueprint, stage),
        "blueprint": copy.deepcopy(dict(blueprint)), "max_tokens": max_tokens,
        "context": context, "response_format": "json",
        "instruction": (
            f"只规划本阶段：{stage_labels[stage]}。沿用作者题材、文风和读者承诺；"
            "保持所有卷、章、场景ID和数量，禁止改写先前已确认的阶段，禁止写正文。"
            "只返回符合本阶段结构的JSON对象。"
        ),
    }


@_transactional
def start_stage(conn: sqlite3.Connection, workflow_id: str,
                callbacks: Mapping[str, Callable[..., Any]] | None,
                expected_stage: str | None = None,
                max_tokens: int | None = None) -> dict[str, Any]:
    """Create exactly one durable job for the current stage."""
    callbacks = callbacks or {}
    row = _load(conn, workflow_id)
    if row["status"] != "active":
        raise PlanningConflict("workflow is not active")
    stage = row["current_stage"]
    if expected_stage is not None and expected_stage != stage:
        raise PlanningConflict("current stage changed")
    if max_tokens is not None:
        max_tokens = _positive_int(max_tokens, "max_tokens", maximum=128_000)
    latest = _latest_attempt(conn, workflow_id, stage)
    if latest and latest["status"] in ACTIVE_ATTEMPT_STATUSES:
        raise PlanningConflict("current stage already has an active or unconfirmed attempt")
    if not callbacks.get("create_job"):
        raise PlanningError("create_job callback is required")
    current_version, source_blueprint = _reader(
        conn, callbacks, row["project_id"], row["branch_id"], row["source_version"],
        _row_json(row, "source_blueprint_json", {}),
    )
    if current_version != row["source_version"]:
        raise PlanningConflict("current blueprint version changed")
    if _hash({"version": current_version, "blueprint": source_blueprint}) != row["source_hash"]:
        raise PlanningConflict("current blueprint content changed")
    current_blueprint = _row_json(row, "blueprint_json", {})
    attempt_no = (latest["attempt_no"] + 1) if latest else 1
    attempt_id = _id("planatt_")
    source_hash = row["source_hash"]
    now = _now()
    conn.execute(
        """
        INSERT INTO app_planning_attempt(
          id,workflow_id,stage,attempt_no,status,job_id,source_version,source_hash,
          candidate_json,candidate_hash,max_tokens,error,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            attempt_id, workflow_id, stage, attempt_no, "queued", None, current_version,
            source_hash, None, None, max_tokens, None, now, now,
        ),
    )
    payload = _source_payload(row, stage, current_blueprint, current_version, source_hash, max_tokens)
    payload["attempt_id"] = attempt_id
    payload["context"]["planning_workflow"]["attempt_id"] = attempt_id
    # The callback only inserts the durable local job in this transaction.
    # A worker can dispatch it after the caller commits; a failed insert rolls
    # back the attempt and any quota reservation with it.
    job = _invoke(callbacks["create_job"], conn=conn, payload=payload,
                  workflow_id=workflow_id, attempt_id=attempt_id, stage=stage)
    if isinstance(job, Mapping):
        job_id = job.get("id") or job.get("job_id")
        job_status = str(job.get("status") or "queued")
    else:
        job_id, job_status = job, "queued"
    if not isinstance(job_id, str) or not job_id:
        raise PlanningError("create_job must return a job id")
    if job_status not in {"queued", "running", "complete", "failed", "unknown"}:
        job_status = "queued"
    if job_status == "complete":
        job_status = "queued"
    conn.execute(
        "UPDATE app_planning_attempt SET job_id=?,status=?,updated_at=? WHERE id=?",
        (job_id, job_status, _now(), attempt_id),
    )
    _event(conn, workflow_id, "stage.started", {
        "stage": stage, "attempt_id": attempt_id, "job_id": job_id,
        "source_version": current_version,
    }, attempt_id, callbacks)
    return get_workflow(conn, workflow_id)


def _normalize_job(raw: Any) -> tuple[str, Any, str | None]:
    if not isinstance(raw, Mapping):
        raise PlanningError("get_job must return an object")
    status = str(raw.get("status") or raw.get("state") or "unknown").lower()
    if status in {"succeeded", "success", "done"}:
        status = "complete"
    if status in {"error", "aborted"}:
        status = "failed"
    if status not in {"queued", "running", "complete", "failed", "unknown", "canceled"}:
        status = "unknown"
    result = raw.get("result")
    result = result if isinstance(result, Mapping) else raw
    billing = result.get("billing_state", raw.get("billing_state"))
    audit = result.get("request_audit")
    audit = audit if isinstance(audit, Mapping) else {}
    dispatched = result.get("request_dispatched", raw.get("request_dispatched", audit.get("dispatched")))
    if status in {"failed", "canceled"} and (
            billing == "unknown" or billing is None and dispatched is True):
        status = "unknown"
    candidate = result.get("structured", raw.get("candidate"))
    error = raw.get("error") or raw.get("message")
    if status == "complete":
        if not (result.get("safe_to_apply") is True
                and result.get("completion_state") == "complete"
                and result.get("finish_reason") == "stop"):
            status = "unknown" if billing == "unknown" else "failed"
            error = "complete job lacks complete structured-response evidence"
    return status, candidate, str(error) if error else None


@_transactional
def refresh_stage(conn: sqlite3.Connection, workflow_id: str,
                  callbacks: Mapping[str, Callable[..., Any]] | None) -> dict[str, Any]:
    """Synchronize the latest durable job; unknown never becomes a retry."""
    callbacks = callbacks or {}
    row = _load(conn, workflow_id)
    stage = row["current_stage"]
    attempt = _latest_attempt(conn, workflow_id, stage)
    if attempt is None:
        return get_workflow(conn, workflow_id)
    if row["status"] != "active" or attempt["status"] in {"confirmed", "failed", "canceled", "complete"}:
        return get_workflow(conn, workflow_id)
    if not callbacks.get("get_job"):
        raise PlanningError("get_job callback is required")
    raw = _invoke(callbacks["get_job"], conn=conn, job_id=attempt["job_id"],
                  workflow_id=workflow_id, attempt_id=attempt["id"])
    if isinstance(raw, Mapping):
        if raw.get("project_id", row["project_id"]) != row["project_id"] or \
                raw.get("branch_id", row["branch_id"]) != row["branch_id"]:
            raise PlanningNotFound("job not found in workflow scope")
    status, candidate, error = _normalize_job(raw)
    if status == "complete":
        if not isinstance(candidate, Mapping):
            status, error = "failed", "complete job did not contain a candidate"
        else:
            candidate = copy.deepcopy(dict(candidate))
            try:
                candidate = _validate_candidate(stage, candidate, _row_json(row, "blueprint_json", {}))
            except (ValueError, TypeError) as exc:
                status, error = "failed", str(exc)
            else:
                candidate_hash = _hash(candidate)
                conn.execute(
                    "UPDATE app_planning_attempt SET status='complete',candidate_json=?,candidate_hash=?,error=NULL,updated_at=? WHERE id=?",
                    (_json(candidate), candidate_hash, _now(), attempt["id"]),
                )
                _event(conn, workflow_id, "stage.complete", {
                    "stage": stage, "attempt_id": attempt["id"], "candidate_hash": candidate_hash,
                }, attempt["id"], callbacks)
                return get_workflow(conn, workflow_id)
    if attempt["status"] == "unknown" and status == "failed":
        # A missing/failed job after an unknown dispatch is not new evidence
        # that no charge occurred; keep the no-retry lock.
        status = "unknown"
    if status == attempt["status"] and error == attempt["error"]:
        return get_workflow(conn, workflow_id)
    conn.execute(
        "UPDATE app_planning_attempt SET status=?,error=?,updated_at=? WHERE id=?",
        (status, error, _now(), attempt["id"]),
    )
    _event(conn, workflow_id, f"stage.{status}", {
        "stage": stage, "attempt_id": attempt["id"], "error": error,
    }, attempt["id"], callbacks)
    return get_workflow(conn, workflow_id)


refresh_workflow = refresh_stage
refresh = refresh_stage


def _require_keys(item: Mapping[str, Any], required: tuple[str, ...], label: str) -> None:
    missing = [key for key in required if key not in item]
    if missing:
        raise PlanningError(f"{label} missing: {', '.join(missing)}")


def _validate_ids(values: list[str], label: str) -> None:
    if any(not isinstance(value, str) or not value for value in values):
        raise PlanningError(f"{label} contains an invalid id")
    if len(set(values)) != len(values):
        raise PlanningError(f"{label} contains duplicate ids")
    for value in values:
        validate_id(value, label)


def _validate_candidate(stage: str, candidate: Mapping[str, Any], blueprint: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise PlanningError("stage candidate must be an object")
    candidate = copy.deepcopy(dict(candidate))
    try:
        validate_json(candidate, schema()[stage])
    except ValueError as exc:
        raise PlanningError(f"invalid {stage} response: {exc}") from exc
    if stage == "overview":
        _require_keys(candidate, ("premise", "genre", "audience", "tone", "contract"), "overview")
        for key, limit in (("premise", 10000), ("genre", 100), ("audience", 1000),
                           ("tone", 3000), ("contract", 20000)):
            _text(candidate[key], f"overview.{key}", limit)
        return {key: candidate[key] for key in ("premise", "genre", "audience", "tone", "contract")}
    volumes = candidate.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise PlanningError(f"{stage}.volumes must be a non-empty list")
    base_volumes = {item.get("id"): item for item in blueprint.get("volumes", [])}
    _validate_ids([item.get("id") for item in volumes], f"{stage}.volume ids")
    if set(base_volumes) != {item.get("id") for item in volumes}:
        raise PlanningConflict(f"{stage} candidate volume ids do not match current blueprint")
    if stage == "volumes":
        normalized = []
        for volume in volumes:
            if not isinstance(volume, Mapping):
                raise PlanningError("volume candidate must be an object")
            _require_keys(volume, ("id", "title", "summary"), "volume")
            normalized.append({
                "id": volume["id"], "title": _text(volume["title"], "volume.title", 500),
                "summary": _text(volume["summary"], "volume.summary", 20000),
            })
        return {"volumes": normalized}
    normalized_volumes = []
    for volume in volumes:
        if not isinstance(volume, Mapping):
            raise PlanningError("volume candidate must be an object")
        expected_chapters = {item.get("id") for item in base_volumes[volume["id"]].get("chapters", [])}
        chapters = volume.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            raise PlanningError(f"{stage}.chapters must be a non-empty list")
        _validate_ids([item.get("id") for item in chapters if isinstance(item, Mapping)],
                      f"{stage}.chapter ids")
        if expected_chapters != {item.get("id") for item in chapters}:
            raise PlanningConflict(f"{stage} candidate chapter ids do not match current blueprint")
        normalized_chapters = []
        for chapter in chapters:
            if not isinstance(chapter, Mapping):
                raise PlanningError("chapter candidate must be an object")
            if stage == "chapters":
                _require_keys(chapter, ("id", "title", "goal", "pov", "hook"), "chapter")
                normalized_chapters.append({
                    "id": chapter["id"], "title": _text(chapter["title"], "chapter.title", 500),
                    "goal": _text(chapter["goal"], "chapter.goal", 5000),
                    "pov": _text(chapter["pov"], "chapter.pov", 500),
                    "hook": _text(chapter["hook"], "chapter.hook", 5000),
                })
                continue
            scenes = chapter.get("scenes")
            if not isinstance(scenes, list) or not scenes:
                raise PlanningError("scenes must be a non-empty list")
            base_chapter = next(item for item in base_volumes[volume["id"]]["chapters"]
                                if item.get("id") == chapter["id"])
            expected_scenes = {item.get("id") for item in base_chapter.get("scenes", [])}
            _validate_ids([item.get("id") for item in scenes if isinstance(item, Mapping)], "scene ids")
            if expected_scenes != {item.get("id") for item in scenes}:
                raise PlanningConflict("scene ids do not match current blueprint")
            normalized_scenes = []
            for scene in scenes:
                if not isinstance(scene, Mapping):
                    raise PlanningError("scene candidate must be an object")
                _require_keys(scene, ("id", "title", "goal", "location", "pov"), "scene")
                normalized_scenes.append({
                    "id": scene["id"], "title": _text(scene["title"], "scene.title", 500),
                    "goal": _text(scene["goal"], "scene.goal", 5000),
                    "location": _text(scene["location"], "scene.location", 500),
                    "pov": _text(scene["pov"], "scene.pov", 500),
                })
            normalized_chapters.append({"id": chapter["id"], "scenes": normalized_scenes})
        normalized_volumes.append({"id": volume["id"], "chapters": normalized_chapters})
    return {"volumes": normalized_volumes}


def _merge_stage(blueprint: Mapping[str, Any], stage: str, candidate: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(blueprint))
    if stage == "overview":
        result.update(candidate)
        return result
    if stage == "volumes":
        by_id = {item["id"]: item for item in candidate["volumes"]}
        for volume in result["volumes"]:
            volume.update(by_id[volume["id"]])
        return result
    volume_by_id = {item["id"]: item for item in candidate["volumes"]}
    for volume in result["volumes"]:
        incoming = volume_by_id[volume["id"]]
        chapter_by_id = {item["id"]: item for item in incoming["chapters"]}
        for chapter in volume["chapters"]:
            updated = chapter_by_id[chapter["id"]]
            if stage == "chapters":
                chapter.update(updated)
            else:
                scene_by_id = {item["id"]: item for item in updated["scenes"]}
                chapter["scenes"] = [scene_by_id[scene["id"]] for scene in chapter["scenes"]]
    return result


@_transactional
def confirm_stage(conn: sqlite3.Connection, workflow_id: str,
                  callbacks: Mapping[str, Callable[..., Any]] | None = None, *,
                  expected_stage: str | None = None,
                  attempt_id: str | None = None, candidate_hash: str | None = None,
                  expected_version: int | None = None) -> dict[str, Any]:
    """Confirm one complete candidate and advance the workflow atomically."""
    callbacks = callbacks or {}
    row = _load(conn, workflow_id)
    if row["status"] != "active":
        raise PlanningConflict("workflow is not active")
    stage = row["current_stage"]
    if expected_stage is not None and expected_stage != stage:
        raise PlanningConflict("current stage changed")
    if expected_version is not None and (
            isinstance(expected_version, bool) or not isinstance(expected_version, int)):
        raise PlanningError("expected_version must be an integer")
    if expected_version is not None and expected_version != row["version"]:
        raise PlanningConflict("workflow version changed")
    attempt = _latest_attempt(conn, workflow_id, stage)
    if attempt is None or attempt["status"] != "complete":
        raise PlanningConflict("current stage has no complete candidate")
    if attempt_id is None or candidate_hash is None:
        raise PlanningError("attempt_id and candidate_hash are required to confirm a stage")
    if attempt_id != attempt["id"] or candidate_hash != attempt["candidate_hash"]:
        raise PlanningConflict("confirmation belongs to a different stage attempt")
    current_version, source_blueprint = _reader(
        conn, callbacks, row["project_id"], row["branch_id"], row["source_version"],
        _row_json(row, "source_blueprint_json", {}),
    )
    if current_version != attempt["source_version"]:
        raise PlanningConflict("blueprint changed while stage was running")
    if attempt["source_hash"] != row["source_hash"]:
        raise PlanningConflict("stage attempt source snapshot changed")
    if current_version != row["source_version"]:
        raise PlanningConflict("workflow source blueprint is stale")
    if _hash({"version": current_version, "blueprint": source_blueprint}) != row["source_hash"]:
        raise PlanningConflict("source blueprint content changed")
    current_blueprint = _row_json(row, "blueprint_json", {})
    saved_candidate = _row_json(attempt, "candidate_json", None)
    if _hash(saved_candidate) != attempt["candidate_hash"]:
        raise PlanningConflict("saved candidate changed after completion")
    normalized = _validate_candidate(stage, saved_candidate, current_blueprint)
    merged = _merge_stage(current_blueprint, stage, normalized)
    next_index = row["stage_index"] + 1
    final = next_index == len(STAGES)
    if final:
        try:
            merged = validate_blueprint(merged)
        except (ValueError, TypeError) as exc:
            raise PlanningError(f"final blueprint is invalid: {exc}") from exc
    now = _now()
    attempt_updated = conn.execute(
        "UPDATE app_planning_attempt SET status='confirmed',updated_at=? WHERE id=? AND status='complete'",
        (now, attempt["id"]),
    )
    if attempt_updated.rowcount != 1:
        raise PlanningConflict("stage attempt changed concurrently")
    workflow_updated = conn.execute(
        """
        UPDATE app_planning_workflow SET
          status=?,current_stage=?,stage_index=?,version=?,
          blueprint_json=?,final_blueprint_json=?,source_version=?,source_hash=?,updated_at=?
        WHERE id=? AND status='active' AND version=?
        """,
        (
            "completed" if final else "active",
            STAGES[next_index] if not final else STAGES[-1],
            next_index, row["version"] + 1, _json(merged),
            _json(merged) if final else None, current_version,
            row["source_hash"], now,
            workflow_id, row["version"],
        ),
    )
    if workflow_updated.rowcount != 1:
        raise PlanningConflict("workflow changed concurrently")
    _event(conn, workflow_id, "stage.confirmed", {
        "stage": stage, "attempt_id": attempt["id"], "candidate_hash": attempt["candidate_hash"],
        "next_stage": STAGES[next_index] if not final else None, "final": final,
    }, attempt["id"], callbacks)
    return get_workflow(conn, workflow_id)


@_transactional
def cancel_workflow(conn: sqlite3.Connection, workflow_id: str,
                    callbacks: Mapping[str, Callable[..., Any]] | None = None,
                    reason: str = "author_cancelled") -> dict[str, Any]:
    callbacks = callbacks or {}
    row = _load(conn, workflow_id)
    if row["status"] == "canceled":
        return get_workflow(conn, workflow_id)
    if row["status"] == "completed":
        raise PlanningConflict("completed workflow cannot be canceled")
    attempt = _latest_attempt(conn, workflow_id, row["current_stage"])
    if attempt and attempt["status"] in {"queued", "running", "complete", "unknown"}:
        cancel = callbacks.get("cancel_job")
        if attempt["status"] == "complete":
            conn.execute("UPDATE app_planning_attempt SET status='canceled',updated_at=? WHERE id=?",
                         (_now(), attempt["id"]))
        elif cancel and attempt["job_id"]:
            result = _invoke(cancel, conn=conn, job_id=attempt["job_id"], workflow_id=workflow_id,
                             attempt_id=attempt["id"])
            cancel_unknown = (
                attempt["status"] == "unknown" or attempt["status"] == "running"
                or isinstance(result, Mapping) and (
                    str(result.get("status", "")).lower() == "unknown"
                    or result.get("billing_state") == "unknown")
            )
            if cancel_unknown:
                conn.execute("UPDATE app_planning_attempt SET status='unknown',error=?,updated_at=? WHERE id=?",
                             ("cancel outcome unknown", _now(), attempt["id"]))
            else:
                conn.execute("UPDATE app_planning_attempt SET status='canceled',updated_at=? WHERE id=?",
                             (_now(), attempt["id"]))
        else:
            conn.execute("UPDATE app_planning_attempt SET status='unknown',updated_at=? WHERE id=?",
                         (_now(), attempt["id"]))
    conn.execute(
        "UPDATE app_planning_workflow SET status='canceled',cancel_reason=?,version=version+1,updated_at=? WHERE id=? AND status='active'",
        (reason, _now(), workflow_id),
    )
    _event(conn, workflow_id, "workflow.canceled", {"reason": reason}, callbacks=callbacks)
    return get_workflow(conn, workflow_id)


__all__ = [
    "ACTIVE_ATTEMPT_STATUSES", "DEFAULT_COUNTS", "PlanningConflict",
    "PlanningDispatchUnknown", "PlanningError", "PlanningNotFound", "STAGES",
    "cancel_workflow", "confirm_stage", "create_workflow", "get_workflow",
    "install_schema", "refresh", "refresh_stage", "refresh_workflow", "schema",
    "start_stage",
]
