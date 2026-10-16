"""Private, source-bound dependencies for the materials in a frozen request."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Mapping

from moyu_records import decode_scope


_CHANGED = "本次使用的资料已变化，请按当前资料生成新候选；原候选仍保留供阅读"
_LEGACY = "旧任务缺少完整资料校验记录，仅供阅读，请按当前资料生成新候选"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_QUERIES = {
    "project": (
        "SELECT p.id,p.name,m.summary,m.genre,m.goal FROM project p "
        "LEFT JOIN app_project_meta m ON m.project_id=p.id WHERE p.id=?",
        False,
    ),
    "chapter": (
        "SELECT n.id,n.project_id,n.branch_id,n.title,n.ordinal,r.id revision_id,r.content_hash,"
        "m.status,m.chapter_goal,m.scene_plan,m.pov,m.foreshadowing_op,m.hook,m.recap,m.next_brief,"
        "s.volume_id,s.plan_id,s.scenes_json FROM document_node n "
        "JOIN document_revision r ON r.id=(SELECT rr.id FROM document_revision rr "
        "WHERE rr.document_node_id=n.id AND rr.branch_id=n.branch_id ORDER BY rr.rowid DESC LIMIT 1) "
        "LEFT JOIN app_chapter_meta m ON m.chapter_id=n.id "
        "LEFT JOIN app_chapter_structure s ON s.chapter_id=n.id "
        "WHERE n.project_id=? AND n.branch_id=? AND n.id=? AND n.kind='chapter' "
        "AND NOT EXISTS(SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id)",
        True,
    ),
    "record": (
        "SELECT e.id,e.project_id,e.kind,e.canonical_name,e.status,e.aliases_json,m.description,s.scope_json "
        "FROM entity e LEFT JOIN app_entity_meta m ON m.entity_id=e.id "
        "LEFT JOIN app_entity_scope s ON s.entity_id=e.id WHERE e.project_id=? AND e.id=?",
        False,
    ),
    "memory": (
        "SELECT cm.id,cm.project_id,cm.branch_id,cm.entity_id,cm.kind,cm.payload_json,cm.status,"
        "cm.confidence,cm.source_span_id FROM candidate_memory cm "
        "WHERE cm.project_id=? AND cm.branch_id=? AND cm.id=?",
        True,
    ),
    "character_state": (
        "SELECT c.* FROM character_state c WHERE c.project_id=? AND c.branch_id=? AND c.id=?",
        True,
    ),
    "relationship": (
        "SELECT r.id,r.project_id,r.branch_id,r.from_entity_id,r.to_entity_id,r.relation_type,"
        "r.note,r.status,r.evidence_json FROM app_story_relation r "
        "WHERE r.project_id=? AND r.branch_id=? AND r.id=?",
        True,
    ),
    "timeline": (
        "SELECT t.id,t.project_id,t.branch_id,t.title,t.event_time,t.chapter_id,t.entity_ids_json,"
        "t.summary,t.status,t.ordinal,t.evidence_json FROM app_timeline_event t "
        "WHERE t.project_id=? AND t.branch_id=? AND t.id=?",
        True,
    ),
    "foreshadowing": (
        "SELECT f.id,f.project_id,f.branch_id,f.plot_id,f.chapter_id,f.kind,f.note,f.evidence_json,"
        "p.stage,p.text,p.archived FROM app_foreshadow_link f "
        "JOIN app_plot_item p ON p.id=f.plot_id WHERE f.project_id=? AND f.branch_id=? AND f.id=?",
        True,
    ),
    "assertion": (
        "SELECT a.id,a.project_id,a.branch_id,a.subject_entity_id,a.predicate,a.object_json,"
        "a.epistemic_mode,a.state,a.valid_start,a.valid_end,a.confidence,a.evidence_json "
        "FROM assertion a WHERE a.project_id=? AND a.branch_id=? AND a.id=?",
        True,
    ),
    "plot_thread": (
        "SELECT p.id,p.project_id,p.branch_id,p.status,p.payload_json,p.severity "
        "FROM plot_thread p WHERE p.project_id=? AND p.branch_id=? AND p.id=?",
        True,
    ),
    "plot": (
        "SELECT p.id,p.project_id,p.branch_id,p.stage,p.text,p.archived "
        "FROM app_plot_item p WHERE p.project_id=? AND p.branch_id=? AND p.id=?",
        True,
    ),
    "reference_section": (
        "SELECT s.id,s.document_id,s.project_id,s.branch_id,s.ordinal,s.title,s.body,s.source_json,"
        "d.filename,d.title document_title,d.source_sha256,d.text_sha256,d.metadata_json "
        "FROM app_reference_section s JOIN app_reference_document d ON d.id=s.document_id "
        "AND d.project_id=s.project_id AND d.branch_id=s.branch_id "
        "WHERE s.project_id=? AND s.branch_id=? AND s.id=?",
        True,
    ),
}
_STRUCTURE = {
    "relationships": "relationship", "timeline": "timeline",
    "foreshadowing": "foreshadowing", "conflicts": "assertion",
    "character_states": "character_state", "plot_threads": "plot_thread",
}
_TABLES = {
    "chapter": "document_node", "record": "entity", "memory": "candidate_memory",
    "character_state": "character_state", "relationship": "app_story_relation",
    "timeline": "app_timeline_event", "foreshadowing": "app_foreshadow_link",
    "assertion": "assertion", "plot_thread": "plot_thread", "plot": "app_plot_item",
}


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row(conn, query, parameters):
    cursor = conn.execute(query, parameters)
    row = cursor.fetchone()
    if row is None:
        raise ValueError(_CHANGED)
    return dict(zip((column[0] for column in cursor.description), row))


def _decoded(row):
    result = dict(row)
    for key, value in result.items():
        if key == "scope_json":
            result[key] = decode_scope(value)
        elif key.endswith("_json"):
            try:
                result[key] = json.loads(value) if isinstance(value, str) else value
            except (TypeError, ValueError):
                raise ValueError("本次资料的保存内容损坏，请先核对资料") from None
    return result


def _identity(conn, project, entity_id):
    if not entity_id:
        return None
    return _row(conn, "SELECT id,kind,canonical_name,status FROM entity WHERE project_id=? AND id=?",
                (project, entity_id))


def _chapter_position(conn, project, branch, chapter_id):
    # Match the active order used by chapter_rows and context scope decisions.
    return _row(conn,
        "SELECT * FROM (SELECT n.id,n.title,n.ordinal,"
        "ROW_NUMBER() OVER(ORDER BY n.ordinal,n.id) chapter_number FROM document_node n "
        "WHERE n.project_id=? AND n.branch_id=? AND n.kind='chapter' "
        "AND NOT EXISTS(SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id) "
        "AND EXISTS(SELECT 1 FROM document_revision r WHERE r.document_node_id=n.id)) "
        "WHERE id=?", (project, branch, chapter_id))


def _span(conn, project, branch, span_id):
    if not span_id:
        return None
    value = _row(conn,
        "SELECT s.id,s.revision_id,s.document_node_id,s.start_codepoint,s.end_codepoint,"
        "s.quote_hash,s.quote,s.status,"
        "(SELECT r.id FROM document_revision r WHERE r.document_node_id=n.id "
        "AND r.branch_id=n.branch_id ORDER BY r.rowid DESC LIMIT 1) current_revision_id,"
        "EXISTS(SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id) deleted "
        "FROM source_span s JOIN document_node n ON n.id=s.document_node_id "
        "WHERE s.project_id=? AND n.project_id=? AND n.branch_id=? AND s.id=?",
        (project, project, branch, span_id))
    value["chapter"] = _chapter_position(conn, project, branch, value["document_node_id"])
    return value


def _raw_source(conn, project, branch, kind, identifier):
    query, branched = _QUERIES[kind]
    parameters = (project,) if kind == "project" else (
        (project, branch, identifier) if branched else (project, identifier))
    return _row(conn, query, parameters)


def _source(conn, project, branch, kind, identifier):
    value = _decoded(_raw_source(conn, project, branch, kind, identifier))
    if kind == "chapter":
        value["scope_position"] = _chapter_position(conn, project, branch, identifier)
    if kind in {"timeline", "foreshadowing"} and value.get("chapter_id"):
        value["chapter"] = _chapter_position(conn, project, branch, value["chapter_id"])
    if kind == "memory":
        value["entity"] = _identity(conn, project, value.get("entity_id"))
        value["span"] = _span(conn, project, branch, value.get("source_span_id"))
        rows = conn.execute(
            "SELECT a.id,a.state,a.predicate,a.object_json,a.epistemic_mode FROM assertion a "
            "WHERE a.project_id=? AND a.branch_id=? AND EXISTS(SELECT 1 FROM json_each(a.evidence_json) e "
            "WHERE CASE WHEN e.type='object' THEN json_extract(e.value,'$.candidate_memory_id') END=?) "
            "ORDER BY a.id",
            (project, branch, identifier))
        value["assertions"] = [_decoded(dict(row)) for row in rows]
    if kind == "character_state":
        value["entity"] = _identity(conn, project, value.get("entity_id"))
        if value.get("source_assertion_id"):
            value["assertion"] = _source(conn, project, branch, "assertion", value["source_assertion_id"])
    if kind == "assertion":
        value["entity"] = _identity(conn, project, value.get("subject_entity_id"))
    if kind == "relationship":
        value["entities"] = [_identity(conn, project, value.get(field))
                             for field in ("from_entity_id", "to_entity_id")]
    if kind == "timeline":
        value["entities"] = [_identity(conn, project, identifier)
                             for identifier in value.get("entity_ids_json") or []]
    evidence = value.get("evidence_json")
    entries = evidence if isinstance(evidence, list) else [evidence] if isinstance(evidence, dict) else []
    value["evidence_sources"] = [
        _span(conn, project, branch, item["source_span_id"])
        for item in entries if isinstance(item, Mapping) and item.get("source_span_id")
    ]
    return value


def _author_resolution_value(kind, value):
    # Completing a chapter changes workflow state, not the author's explanation.
    if kind == "chapter" and value is not None:
        return {key: child for key, child in value.items() if key != "status"}
    return value


def author_resolution_snapshot(conn: sqlite3.Connection, project: str, branch: str):
    """Bind a legacy author explanation when the old consumed set is unknown."""
    sources = []
    for kind in _QUERIES:
        if kind == "reference_section":
            continue
        if kind == "project":
            identifiers = [project]
        else:
            table = _TABLES[kind]
            branched = _QUERIES[kind][1]
            clause = "project_id=?" + (" AND branch_id=?" if branched else "")
            if kind == "chapter":
                clause += " AND kind='chapter'"
            parameters = (project, branch) if branched else (project,)
            identifiers = [row[0] for row in conn.execute(
                f"SELECT id FROM {table} WHERE {clause} ORDER BY id", parameters)]
        for identifier in identifiers:
            try:
                value = _source(conn, project, branch, kind, identifier)
            except ValueError:
                try:
                    value = _raw_source(conn, project, branch, kind, identifier)
                except ValueError:
                    value = None
            sources.append({"kind": kind, "id": identifier,
                            "source": _author_resolution_value(kind, value)})
    return {"version": 1, "purpose": "author_resolution",
            "project_id": project, "branch_id": branch, "sha256": _hash(sources)}


def _plan_sources(state, structure):
    sources = {}
    collections = [(kind, (state or {}).get(name) or []) for name, kind in (
        ("records", "record"), ("memory", "memory"), ("chapters", "chapter"),
    )]
    collections.extend((kind, (structure or {}).get(name) or []) for name, kind in _STRUCTURE.items())
    collections.extend(("plot", items) for items in ((state or {}).get("plot") or {}).values())
    for kind, items in collections:
        for item in items:
            sources.setdefault(item["id"], set()).add(kind)
    return sources


def capture(conn: sqlite3.Connection, project: str, branch: str, context: Mapping[str, Any],
            *, state: Mapping[str, Any] | None = None, structure: Mapping[str, Any] | None = None):
    """Seal only consumed source identities after the request budget is fitted."""
    identities = {("project", project)}

    def add(kind, item):
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("本次资料缺少来源身份，请重新打开当前作品")
        identities.add((kind, item["id"]))
        if kind == "assertion" and item.get("memory_id"):
            identities.add(("memory", item["memory_id"]))

    if context.get("current_chapter"):
        add("chapter", context["current_chapter"])
    for collection, kind in (
        ("retrieved_chapters", "chapter"), ("previous_chapters", "chapter"),
        ("confirmed_memory", "memory"), ("story_cards", "record"),
    ):
        for item in context.get(collection) or []:
            add(kind, item)
    for name, kind in _STRUCTURE.items():
        for item in (context.get("story_structure") or {}).get(name) or []:
            add(kind, item)
    for items in (context.get("plot") or {}).values():
        for item in items:
            add("plot", item)
    plan_sources = _plan_sources(state, structure)
    for item in context.get("author_plans") or []:
        kinds = plan_sources.get(item.get("id"))
        if not kinds:
            raise ValueError("规划资料缺少可校验的来源，请重新打开当前作品")
        for kind in sorted(kinds):
            add(kind, item)
    for item in context.get("reference_passages") or []:
        if not isinstance(item, Mapping):
            raise ValueError("本次参考片段缺少可校验的来源，请重新选择。")
        add("reference_section", {"id": item.get("section_id")})
    return {
        "version": 1, "project_id": project, "branch_id": branch,
        "dependencies": [
            {"kind": kind, "id": identifier, "sha256": _hash(_source(conn, project, branch, kind, identifier))}
            for kind, identifier in sorted(identities)
        ],
    }


def _refresh(conn: sqlite3.Connection, project: str, branch: str, marker: Mapping[str, Any],
             *, allow_unavailable: bool = False, author_resolution: bool = False):
    """Read current values for the same identities; never rebuild the prompt."""
    if (not isinstance(marker, Mapping) or type(marker.get("version")) is not int
            or marker["version"] != 1 or marker.get("project_id") != project
            or marker.get("branch_id") != branch or "purpose" in marker
            or not isinstance(marker.get("dependencies"), list)):
        raise ValueError(_LEGACY)
    dependencies, seen = [], set()
    for item in marker["dependencies"]:
        if (not isinstance(item, Mapping) or item.get("kind") not in _QUERIES
                or not isinstance(item.get("id"), str) or not item["id"]
                or not isinstance(item.get("sha256"), str) or not _HASH.fullmatch(item["sha256"])):
            raise ValueError(_LEGACY)
        kind, identifier = item["kind"], item["id"]
        if (kind, identifier) in seen or (kind == "project" and identifier != project):
            raise ValueError(_LEGACY)
        seen.add((kind, identifier))
        try:
            source = _source(conn, project, branch, kind, identifier)
        except ValueError:
            if not allow_unavailable:
                raise
            # A current author explanation can acknowledge a deleted source.
            # This marker never authorizes model dispatch or candidate use.
            try:
                raw = _raw_source(conn, project, branch, kind, identifier)
            except ValueError:
                raw = None
            if author_resolution:
                raw = _author_resolution_value(kind, raw)
            dependencies.append({"kind": kind, "id": identifier,
                                 "sha256": _hash(raw), "unavailable": True})
        else:
            if author_resolution:
                source = _author_resolution_value(kind, source)
            dependencies.append({"kind": kind, "id": identifier, "sha256": _hash(source)})
    if ("project", project) not in seen:
        raise ValueError(_LEGACY)
    current = {"version": 1, "project_id": project, "branch_id": branch, "dependencies": dependencies}
    if author_resolution:
        current["purpose"] = "author_resolution"
    return current


def refresh(conn: sqlite3.Connection, project: str, branch: str, marker: Mapping[str, Any],
            *, allow_unavailable: bool = False):
    return _refresh(conn, project, branch, marker, allow_unavailable=allow_unavailable)


def author_resolution_materials(conn: sqlite3.Connection, project: str, branch: str,
                               marker: Mapping[str, Any]):
    """Bind an author decision without authorizing model dispatch or adoption."""
    return _refresh(conn, project, branch, marker, allow_unavailable=True, author_resolution=True)


def validate(conn: sqlite3.Connection, project: str, branch: str, marker: Mapping[str, Any]):
    if refresh(conn, project, branch, marker) != marker:
        raise ValueError(_CHANGED)


def validate_reference_context(conn: sqlite3.Connection, project: str, branch: str,
                               context: Mapping[str, Any]) -> None:
    """Require the exact quote and its consumed dependency to remain together."""
    from moyu_import import REFERENCE_SELECTION_FIELDS, resolve_reference_selections

    passages = context.get("reference_passages", [])
    marker = context.get("_material_source")
    dependencies = marker.get("dependencies", []) if isinstance(marker, Mapping) else []
    if not isinstance(dependencies, list):
        raise ValueError(_LEGACY)
    bound = [item.get("id") for item in dependencies
             if isinstance(item, Mapping) and item.get("kind") == "reference_section"]
    if not isinstance(passages, list) or len(passages) > 1:
        raise ValueError("本次参考片段记录损坏，请按当前原句生成新候选。")
    selections = []
    for item in passages:
        if not isinstance(item, Mapping) or not REFERENCE_SELECTION_FIELDS <= set(item):
            raise ValueError("本次参考片段来源缺失，请重新选择原句生成新候选。")
        selections.append({key: item[key] for key in REFERENCE_SELECTION_FIELDS})
    if bound != [item["section_id"] for item in selections]:
        raise ValueError("本次参考片段与来源记录不一致，请重新选择原句生成新候选。")
    if passages and resolve_reference_selections(
            conn, {"project_id": project, "branch_id": branch}, selections) != passages:
        raise ValueError("本次参考原句或来源已变化，请按当前资料生成新候选。")


def validate_frozen_reference_context(context: Mapping[str, Any], audit: Any, *,
                                      input_hash: str | None = None,
                                      prompt_hash: str | None = None,
                                      require_full_request: bool = False) -> None:
    """Keep the stored consumed set and the actual frozen request inseparable."""
    issue = "本次保存的材料或完整请求校验失败，请按当前资料生成新候选；原候选仍保留供阅读。"
    if input_hash is not None:
        encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != input_hash:
            raise ValueError(issue)
    payload = audit.get("payload") if isinstance(audit, Mapping) else None
    messages = payload.get("messages") if isinstance(payload, Mapping) else None
    materials = []
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, Mapping) else None
            if (message.get("role") if isinstance(message, Mapping) else None) != "user":
                continue
            if not isinstance(content, str) or not content.startswith("<manuscript>\n"):
                continue
            try:
                value, end = json.JSONDecoder().raw_decode(content[len("<manuscript>\n"):])
            except ValueError:
                continue
            if (isinstance(value, dict)
                    and content[len("<manuscript>\n") + end:].startswith("\n</manuscript>\n")):
                materials.append(value)
    marker = context.get("_material_source")
    dependencies = marker.get("dependencies", []) if isinstance(marker, Mapping) else []
    context_has_reference = bool(context.get("reference_passages")) or (
        isinstance(dependencies, list) and any(
            isinstance(item, Mapping) and item.get("kind") == "reference_section"
            for item in dependencies))
    sent_has_reference = any(bool(item.get("reference_passages")) for item in materials)
    if not context_has_reference and not sent_has_reference and not require_full_request:
        return
    if (len(materials) != 1
            or materials[0].get("reference_passages", []) != context.get("reference_passages", [])):
        raise ValueError(issue)
    if materials[0] != {key: value for key, value in context.items() if not key.startswith("_")}:
        raise ValueError(issue)
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        encoded_messages = json.dumps(messages, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError(issue) from None
    request_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if (audit.get("request_sha256") != request_hash
            or audit.get("messages_sha256") != hashlib.sha256(encoded_messages.encode("utf-8")).hexdigest()
            or (prompt_hash is not None and prompt_hash != request_hash)):
        raise ValueError(issue)
