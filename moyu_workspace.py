"""Request-local manuscript identity and project planning primitives."""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any


_SCOPE = contextvars.ContextVar("moyu_workspace_scope", default=None)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def current_scope():
    return _SCOPE.get()


@contextmanager
def use_scope(project_id: str, branch_id: str, actor_id: str):
    token = _SCOPE.set((project_id, branch_id, actor_id))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def identifier(prefix=""):
    return prefix + uuid.uuid4().hex


def validate_id(value, label="id"):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def install_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS app_workspace(
          project_id TEXT PRIMARY KEY REFERENCES project(id),
          archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_blueprint(
          project_id TEXT NOT NULL REFERENCES project(id),
          branch_id TEXT NOT NULL REFERENCES branch(id),
          version INTEGER NOT NULL DEFAULT 0,
          payload_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
          PRIMARY KEY(project_id,branch_id)
        );
        CREATE TABLE IF NOT EXISTS app_chapter_structure(
          chapter_id TEXT PRIMARY KEY REFERENCES document_node(id),
          project_id TEXT NOT NULL REFERENCES project(id),
          branch_id TEXT NOT NULL REFERENCES branch(id),
          volume_id TEXT NOT NULL DEFAULT '',plan_id TEXT NOT NULL DEFAULT '',
          scenes_json TEXT NOT NULL DEFAULT '[]',updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_candidate_application(
          ai_run_id TEXT PRIMARY KEY REFERENCES ai_run(id),
          project_id TEXT NOT NULL REFERENCES project(id),
          branch_id TEXT NOT NULL REFERENCES branch(id),
          chapter_id TEXT NOT NULL REFERENCES document_node(id),
          request_hash TEXT NOT NULL,source_revision_id TEXT NOT NULL,
          result_revision_id TEXT,decision_id TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_model_settings(
          project_id TEXT PRIMARY KEY REFERENCES project(id),
          settings_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_quota_reservation(
          ai_run_id TEXT PRIMARY KEY REFERENCES ai_run(id),
          project_id TEXT NOT NULL REFERENCES project(id),
          day TEXT NOT NULL,status TEXT NOT NULL,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_assistant_request(
          token_hash TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES project(id),
          branch_id TEXT NOT NULL REFERENCES branch(id),
          job_id TEXT NOT NULL REFERENCES app_ai_job(id),
          created_at TEXT NOT NULL
        );
    """)


def project_rows(conn):
    return [
        dict(row) for row in conn.execute("""
          SELECT p.id,p.name title,COALESCE(m.summary,'') summary,
            COALESCE(m.genre,'') genre,COALESCE(m.goal,50000) goal,
            COALESCE(m.book_status,'writing') book_status,
            COALESCE(w.archived,0) archived,
            COALESCE(w.updated_at,m.updated_at,p.created_at) updated_at,
            (SELECT b.id FROM branch b WHERE b.project_id=p.id
              AND b.status='active' ORDER BY b.rowid LIMIT 1) branch_id,
            (SELECT COUNT(*) FROM document_node n WHERE n.project_id=p.id
              AND n.kind='chapter' AND NOT EXISTS
                (SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id)) chapter_count,
            (SELECT COALESCE(SUM(length(CAST(r.canonical_text AS TEXT))),0)
              FROM document_node n JOIN document_revision r ON r.id=
                (SELECT rr.id FROM document_revision rr WHERE rr.document_node_id=n.id
                  ORDER BY rr.rowid DESC LIMIT 1)
              WHERE n.project_id=p.id AND n.kind='chapter' AND NOT EXISTS
                (SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id)) characters
          FROM project p LEFT JOIN app_project_meta m ON m.project_id=p.id
            LEFT JOIN app_workspace w ON w.project_id=p.id ORDER BY p.rowid
        """)
    ]


def resolve_scope(conn, project, branch=None):
    validate_id(project, "project id")
    if conn.execute("SELECT 1 FROM project WHERE id=?", (project,)).fetchone() is None:
        raise LookupError("project not found")
    if branch:
        validate_id(branch, "branch id")
        row = conn.execute("SELECT id FROM branch WHERE id=? AND project_id=?",
                           (branch, project)).fetchone()
    else:
        row = conn.execute("SELECT id FROM branch WHERE project_id=? AND status='active' ORDER BY rowid LIMIT 1",
                           (project,)).fetchone()
    if row is None:
        raise LookupError("branch not found in this project")
    actor = conn.execute("SELECT id FROM actor WHERE project_id=? AND kind='user' ORDER BY rowid LIMIT 1",
                         (project,)).fetchone()
    return project, row["id"], actor["id"] if actor else "author_" + project


def validate_blueprint(raw: Any):
    if not isinstance(raw, dict):
        raise ValueError("blueprint must be an object")
    result = {}
    limits = {"premise": 10000, "genre": 100, "audience": 1000, "tone": 3000,
              "contract": 20000, "outline": 100000}
    for key, limit in limits.items():
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"invalid blueprint.{key}")
        result[key] = value
    volumes = raw.get("volumes", [])
    if not isinstance(volumes, list) or len(volumes) > 200:
        raise ValueError("blueprint.volumes must contain at most 200 volumes")
    result["volumes"] = []
    seen = set()
    for volume in volumes:
        if not isinstance(volume, dict):
            raise ValueError("volume must be an object")
        item = {"id": volume.get("id") or identifier("vol_"),
                "title": volume.get("title", ""), "summary": volume.get("summary", ""),
                "chapters": []}
        validate_id(item["id"], "volume id")
        if item["id"] in seen:
            raise ValueError("duplicate planning id")
        seen.add(item["id"])
        for key, limit in (("title", 500), ("summary", 20000)):
            if not isinstance(item[key], str) or len(item[key]) > limit:
                raise ValueError("invalid volume " + key)
        chapters = volume.get("chapters", [])
        if not isinstance(chapters, list) or len(chapters) > 1000:
            raise ValueError("invalid planned chapters")
        for chapter in chapters:
            if not isinstance(chapter, dict):
                raise ValueError("planned chapter must be an object")
            planned = {"id": chapter.get("id") or identifier("plan_")}
            validate_id(planned["id"], "plan id")
            if planned["id"] in seen:
                raise ValueError("duplicate planning id")
            seen.add(planned["id"])
            for key, limit in (("title", 500), ("goal", 5000), ("pov", 500), ("hook", 5000)):
                value = chapter.get(key, "")
                if not isinstance(value, str) or len(value) > limit:
                    raise ValueError("invalid planned chapter " + key)
                planned[key] = value
            scenes = chapter.get("scenes", [])
            if not isinstance(scenes, list) or len(scenes) > 200:
                raise ValueError("invalid scene list")
            planned["scenes"] = []
            for scene in scenes:
                if not isinstance(scene, dict):
                    raise ValueError("scene must be an object")
                normalized = {"id": scene.get("id") or identifier("scene_")}
                validate_id(normalized["id"], "scene id")
                if normalized["id"] in seen:
                    raise ValueError("duplicate planning id")
                seen.add(normalized["id"])
                for key, limit in (("title", 500), ("goal", 5000), ("location", 500), ("pov", 500)):
                    value = scene.get(key, "")
                    if not isinstance(value, str) or len(value) > limit:
                        raise ValueError("invalid scene " + key)
                    normalized[key] = value
                planned["scenes"].append(normalized)
            if chapter.get("chapter_id"):
                planned["chapter_id"] = validate_id(chapter["chapter_id"], "chapter id")
            item["chapters"].append(planned)
        result["volumes"].append(item)
    if len(json.dumps(result, ensure_ascii=False)) > 2_000_000:
        raise ValueError("blueprint too large")
    return result


def blueprint_row(conn, project, branch):
    row = conn.execute("SELECT version,payload_json,updated_at FROM app_blueprint WHERE project_id=? AND branch_id=?",
                       (project, branch)).fetchone()
    return {"blueprint": json.loads(row["payload_json"]) if row else validate_blueprint({}),
            "version": row["version"] if row else 0, "updated_at": row["updated_at"] if row else None}


def blueprint_dependency(conn, project, branch):
    current = blueprint_row(conn, project, branch)
    raw = json.dumps(current["blueprint"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"version": current["version"], "blueprint_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest()}


def guidance_source(conn, project, branch, chapter_id):
    from moyu_records import decode_scope

    rows = conn.execute(
        "SELECT n.id,m.chapter_goal,m.scene_plan,m.pov,m.foreshadowing_op,m.hook,m.next_brief,"
        "s.volume_id,s.plan_id,s.scenes_json FROM document_node n "
        "LEFT JOIN app_chapter_meta m ON m.chapter_id=n.id "
        "LEFT JOIN app_chapter_structure s ON s.chapter_id=n.id "
        "WHERE n.project_id=? AND n.branch_id=? AND n.kind='chapter' "
        "AND NOT EXISTS (SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id) ORDER BY n.ordinal,n.rowid",
        (project, branch),
    ).fetchall()
    projection = {}
    for index, row in enumerate(rows):
        if row["id"] == chapter_id:
            projection = dict(row)
            projection["previous_handoff"] = (
                {"chapter_id": rows[index - 1]["id"], "text": rows[index - 1]["next_brief"]}
                if index else None
            )
            break
    # Frozen materials depend on live POV identities and knowledge boundaries.
    projection["alias_dependencies"] = []
    for row in conn.execute(
        "SELECT e.id,e.kind,e.canonical_name,e.aliases_json,s.scope_json FROM entity e "
        "LEFT JOIN app_entity_scope s ON s.entity_id=e.id "
        "WHERE e.project_id=? AND e.status<>'archived' ORDER BY e.id", (project,),
    ):
        try:
            aliases = json.loads(row["aliases_json"] or "[]")
        except (TypeError, ValueError):
            aliases = []
        projection["alias_dependencies"].append({
            "id": row["id"], "type": row["kind"], "name": row["canonical_name"],
            "aliases": aliases, "scope": decode_scope(row["scope_json"]),
        })
    raw = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**blueprint_dependency(conn, project, branch), "chapter_id": chapter_id,
            "chapter_plan_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest()}


def put_blueprint(conn, project, branch, data):
    current = blueprint_row(conn, project, branch)
    expected = data.get("expected_version")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise ValueError("expected_version is required")
    if expected != current["version"]:
        raise RuntimeError("blueprint version conflict")
    payload = validate_blueprint(data.get("blueprint", {}))
    version = current["version"] + 1
    conn.execute("""
       INSERT INTO app_blueprint(project_id,branch_id,version,payload_json,updated_at)
       VALUES(?,?,?,?,?) ON CONFLICT(project_id,branch_id) DO UPDATE SET
       version=excluded.version,payload_json=excluded.payload_json,updated_at=excluded.updated_at
    """, (project, branch, version, json.dumps(payload, ensure_ascii=False), timestamp()))
    return blueprint_row(conn, project, branch)


def remap_portable_state(state, new_project, new_branch, title=None):
    """Clone a manuscript package without sharing any mutable identity."""
    result = copy.deepcopy(state)
    mapping = {}
    for group, prefix in (("chapters", "ch_"), ("records", "ent_"), ("memory", "mem_")):
        for item in result.get(group, []):
            old = item["id"]
            item["id"] = identifier(prefix)
            mapping[old] = item["id"]
    for memory in result.get("memory", []):
        if memory.get("source_chapter_id") in mapping:
            memory["source_chapter_id"] = mapping[memory["source_chapter_id"]]
        for field in ("source_span_id", "assertion_id", "source_revision_id", "current_revision_id"):
            memory.pop(field, None)
    for stage, items in result.get("plot", {}).items():
        for item in items:
            if isinstance(item, dict):
                old = item.get("id")
                item["id"] = identifier("plot_")
                item["stage"] = stage
                if old:
                    mapping[old] = item["id"]
    result["project"]["id"] = new_project
    if title:
        result["project"]["title"] = title
    result["branch_id"] = new_branch
    from moyu_records import FIELDS
    scope_mapping = {**mapping, state.get("project", {}).get("id"): new_project,
                     state.get("branch_id", "main"): new_branch}
    def remap_scope(value):
        if isinstance(value, dict):
            return {key: remap_scope(item) for key, item in value.items()}
        if isinstance(value, list):
            return [remap_scope(item) for item in value]
        return scope_mapping.get(value, value) if isinstance(value, str) else value
    for record in result.get("records", []):
        for field in FIELDS:
            if field in record:
                record[field] = remap_scope(record[field])
    result["head_revision"] = None
    result.pop("version", None)
    result.pop("expected_head", None)
    for chapter in result.get("chapters", []):
        chapter["review_status"] = "unreviewed"
        chapter["review"] = {}
        chapter["reviewed_at"] = None
        chapter.pop("revision_id", None)
    return result, mapping
