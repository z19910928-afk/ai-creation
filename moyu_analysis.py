"""Revision-bound, transactional author decisions for story analysis.

Generation may finish after a manuscript edit. Every save and decision checks
the exact current revision before writing derived state. Callers own the outer
SQLite transaction; savepoints protect a mixed decision from partial effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
from functools import wraps
import json
import re
import sqlite3
import uuid
from typing import Any, Callable, Mapping

from moyu_context import (
    refresh_analysis_status, sent_evidence_ranges, text_hash, validate_chapter_analysis,
    validate_source_quote, validate_story_review,
)
import moyu_materials


class AnalysisConflict(ValueError):
    status = 409


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _scope(scope: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(scope, Mapping) or not isinstance(scope.get("project_id"), str) or not scope["project_id"] or not isinstance(scope.get("branch_id"), str) or not scope["branch_id"]:
        raise ValueError("project and branch scope are required")
    return scope["project_id"], scope["branch_id"]


def _require_material_source(conn, scope, marker):
    if marker is None:
        return
    try:
        moyu_materials.validate(conn, *_scope(scope), marker)
    except ValueError as exc:
        raise AnalysisConflict(str(exc)) from None


def _materials_current(conn, scope, marker):
    try:
        _require_material_source(conn, scope, marker)
    except AnalysisConflict:
        return False
    return True


def _materials_required(conn, scope, run_id, sent_context=None):
    if isinstance(sent_context, Mapping) and any(
            key in sent_context for key in ("_source", "_guidance_source", "_material_source")):
        return True
    if not run_id or not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_run'").fetchone():
        return False
    return bool(conn.execute(
        "SELECT 1 FROM ai_run WHERE id=? AND project_id=? AND branch_id=?",
        (run_id, *_scope(scope))).fetchone())


def _item_sources_required(conn, scope, item):
    return any(key in item for key in (
        "material_source", "guidance_source", "decision_material_source", "decision_guidance_source",
    )) or _materials_required(conn, scope, item.get("run_id"))


def _require_item_materials(conn, scope, item):
    original = item.get("material_source")
    if original is None:
        if _item_sources_required(conn, scope, item):
            raise AnalysisConflict("旧模型报告缺少完整资料记录，仅供阅读，请按当前资料重新生成。")
        return
    try:
        current = moyu_materials.refresh(conn, *_scope(scope), original)
    except ValueError as exc:
        raise AnalysisConflict(str(exc)) from None
    if current != item.get("decision_material_source", original):
        raise AnalysisConflict("本次使用的资料已变化，请按当前资料重新生成；原报告仍保留供阅读。")


def _complete_guidance_source(marker, chapter_id):
    return (
        isinstance(marker, Mapping)
        and type(marker.get("version")) is int and marker["version"] >= 0
        and marker.get("chapter_id") == chapter_id
        and all(isinstance(marker.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", marker[key])
                for key in ("blueprint_hash", "chapter_plan_hash"))
    )


def _require_guidance_source(conn, scope, chapter_id, marker):
    import moyu_workspace
    if not _complete_guidance_source(marker, chapter_id) or dict(marker) != moyu_workspace.guidance_source(
            conn, *_scope(scope), chapter_id):
        raise AnalysisConflict("创作约定或章节计划已经改变，请按当前指南重新生成。")


def _require_item_guidance(conn, scope, item, *, decision_source=False):
    original = item.get("guidance_source")
    if original is None:
        if _item_sources_required(conn, scope, item):
            raise AnalysisConflict("旧模型报告缺少完整创作指南记录，仅供阅读，请按当前指南重新生成。")
        return
    if not _complete_guidance_source(original, item["chapter_id"]):
        raise AnalysisConflict("分析的创作指南记录不完整，请按当前指南重新生成。")
    if "decision_guidance_source" in item:
        _require_guidance_source(conn, scope, item["chapter_id"], item["decision_guidance_source"])
    marker = item.get("decision_guidance_source", original) if decision_source else original
    _require_guidance_source(conn, scope, item["chapter_id"], marker)


def ensure_tables(conn: sqlite3.Connection) -> None:
    # execute(), unlike executescript(), preserves the caller's transaction.
    conn.execute("""CREATE TABLE IF NOT EXISTS app_chapter_analysis(
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL, source_revision_id TEXT NOT NULL, chapter_hash TEXT NOT NULL,
        run_id TEXT, analysis_key TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(project_id,branch_id,analysis_key))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS app_chapter_analysis_scope
        ON app_chapter_analysis(project_id,branch_id,chapter_id,updated_at)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_story_review(
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL, source_revision_id TEXT NOT NULL, chapter_hash TEXT NOT NULL,
        run_id TEXT, review_key TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(project_id,branch_id,review_key))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS app_story_review_scope
        ON app_story_review(project_id,branch_id,chapter_id,updated_at)""")


@contextmanager
def _atomic(conn: sqlite3.Connection):
    # A top-level RELEASE would commit early. Start an outer transaction when
    # needed, leaving its final commit or rollback to the API handler.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    name = "analysis_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + name)
    try:
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


def _current_sources(conn: sqlite3.Connection, scope: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    project_id, branch_id = _scope(scope)
    rows = conn.execute(
        "SELECT n.id,n.project_id,n.branch_id,n.title,n.ordinal,r.id,r.canonical_text "
        "FROM document_node n JOIN document_revision r ON r.id=(SELECT r2.id FROM document_revision r2 "
        "WHERE r2.document_node_id=n.id AND r2.branch_id=n.branch_id ORDER BY r2.rowid DESC LIMIT 1) "
        "WHERE n.project_id=? AND n.branch_id=? AND n.kind='chapter' "
        "AND NOT EXISTS (SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id) ORDER BY n.ordinal,n.rowid",
        (project_id, branch_id),
    ).fetchall()
    chapters, revisions = {}, {}
    for number, row in enumerate(rows, start=1):
        node_id, row_project, row_branch, title, _, revision_id, body = tuple(row)
        text = bytes(body).decode("utf-8", errors="strict") if isinstance(body, (bytes, bytearray, memoryview)) else str(body or "")
        chapters[node_id] = {
            "id": node_id, "title": title, "body": text,
            "project_id": row_project, "branch_id": row_branch, "chapter_number": number,
        }
        revisions[node_id] = revision_id
    return chapters, revisions


def _require_current(
    conn: sqlite3.Connection,
    scope: Mapping[str, Any],
    chapter_id: str,
    revision_id: str,
    chapter_hash: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str]]:
    chapters, revisions = _current_sources(conn, scope)
    chapter = chapters.get(chapter_id)
    if chapter is None or revisions.get(chapter_id) != revision_id or text_hash(chapter["body"]) != chapter_hash:
        raise AnalysisConflict("正文已经改变或章节已删除，请重新分析当前稿件。")
    return chapter, chapters, revisions


def _row(conn: sqlite3.Connection, table: str, scope: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    if table not in {"app_chapter_analysis", "app_story_review"}:
        raise ValueError("invalid analysis table")
    project_id, branch_id = _scope(scope)
    row = conn.execute(
        f"SELECT payload_json,created_at,updated_at,run_id FROM {table} WHERE id=? AND project_id=? AND branch_id=?",
        (item_id, project_id, branch_id),
    ).fetchone()
    if row is None:
        raise ValueError("分析记录不存在。")
    payload = json.loads(row[0])
    payload.update({"created_at": row[1], "updated_at": row[2], "run_id": row[3]})
    return payload


def _save_payload(conn: sqlite3.Connection, table: str, scope: Mapping[str, Any], item: Mapping[str, Any]) -> None:
    project_id, branch_id = _scope(scope)
    conn.execute(
        f"UPDATE {table} SET payload_json=?,updated_at=? WHERE id=? AND project_id=? AND branch_id=?",
        (_json(item), _now(), item["id"], project_id, branch_id),
    )


@_transactional
def save_analysis(
    conn: sqlite3.Connection,
    scope: Mapping[str, Any],
    chapter: Mapping[str, Any],
    run_id: str | None,
    structured: Mapping[str, Any],
) -> dict[str, Any]:
    ensure_tables(conn)
    project_id, branch_id = _scope(scope)
    revision_id = str(chapter.get("source_revision_id") or chapter.get("revision_id") or structured.get("source_revision_id") or "")
    if not revision_id:
        raise ValueError("analysis requires its original source revision")
    digest = text_hash(str(chapter.get("body") or ""))
    current, _, _ = _require_current(conn, scope, str(chapter["id"]), revision_id, digest)
    validated = validate_chapter_analysis(
        structured, current, project_id=project_id, branch_id=branch_id,
        revision_id=revision_id, records=scope.get("records") or [],
    )
    sent_context = scope.get("sent_context") or {}
    marker = sent_context.get("_material_source")
    if marker is None and _materials_required(conn, scope, run_id, sent_context):
        raise AnalysisConflict("章后结果缺少完整资料记录，请按当前资料重新提取。")
    guidance = sent_context.get("_guidance_source")
    if guidance is None and (marker is not None or _materials_required(conn, scope, run_id, sent_context)):
        raise AnalysisConflict("章后结果缺少完整创作指南记录，请按当前指南重新提取。")
    dependencies = {"analysis_key": validated["analysis_key"]}
    if marker is not None:
        _require_material_source(conn, scope, marker)
        validated["material_source"] = copy.deepcopy(marker)
        dependencies["material_source"] = marker
    if "_guidance_source" in sent_context:
        _require_guidance_source(conn, scope, str(chapter["id"]), guidance)
        validated["guidance_source"] = copy.deepcopy(dict(guidance))
        dependencies["guidance_source"] = guidance
    if len(dependencies) > 1:
        validated["analysis_key"] = text_hash(_json(dependencies))
    existing = conn.execute(
        "SELECT id FROM app_chapter_analysis WHERE project_id=? AND branch_id=? AND analysis_key=?",
        (project_id, branch_id, validated["analysis_key"]),
    ).fetchone()
    if existing:
        return _row(conn, "app_chapter_analysis", scope, existing[0])
    analysis_id = "chapter_analysis_" + validated["analysis_key"][:28]
    validated.update({"id": analysis_id, "run_id": run_id, "decisions": [], "status": "pending"})
    for name in ("summary", "handoff"):
        validated[name]["status"] = "pending" if validated[name]["text"].strip() else "empty"
    stamp = _now()
    conn.execute(
        "INSERT INTO app_chapter_analysis(id,project_id,branch_id,chapter_id,source_revision_id,chapter_hash,run_id,analysis_key,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (analysis_id, project_id, branch_id, chapter["id"], revision_id, digest, run_id, validated["analysis_key"], _json(validated), stamp, stamp),
    )
    return _row(conn, "app_chapter_analysis", scope, analysis_id)


def list_analysis(conn: sqlite3.Connection, scope: Mapping[str, Any], chapter_id: str | None = None) -> list[dict[str, Any]]:
    ensure_tables(conn)
    project_id, branch_id = _scope(scope)
    chapters, revisions = _current_sources(conn, scope)
    clauses = "project_id=? AND branch_id=?"
    params: list[Any] = [project_id, branch_id]
    if chapter_id:
        clauses += " AND chapter_id=?"
        params.append(chapter_id)
    rows = conn.execute("SELECT id FROM app_chapter_analysis WHERE " + clauses + " ORDER BY updated_at DESC,id", params).fetchall()
    results = [
        refresh_analysis_status(item, chapters.get(item["chapter_id"]), revisions.get(item["chapter_id"], ""))
        for row in rows for item in [_row(conn, "app_chapter_analysis", scope, row[0])]
    ]
    for item in results:
        try:
            _require_item_materials(conn, scope, item)
            _require_item_guidance(conn, scope, item, decision_source=True)
        except AnalysisConflict:
            item.update({"status": "stale", "stale": True, "review_required": True})
            for candidate in item["candidates"]:
                candidate["stale"] = True
            for name in ("summary", "handoff"):
                item[name]["stale"] = True
    return results


def invalidate_chapter(
    conn: sqlite3.Connection,
    scope: Mapping[str, Any],
    chapter_id: str,
    new_revision_id: str,
    new_hash: str,
) -> dict[str, Any]:
    """Mark derived analysis and all cross-chapter reviews for re-examination."""
    ensure_tables(conn)
    project_id, branch_id = _scope(scope)
    rows = conn.execute(
        "SELECT id FROM app_chapter_analysis WHERE project_id=? AND branch_id=? AND chapter_id=?",
        (project_id, branch_id, chapter_id),
    ).fetchall()
    invalidated = []
    for row in rows:
        item = _row(conn, "app_chapter_analysis", scope, row[0])
        if item["source_revision_id"] == new_revision_id and item["chapter_hash"] == new_hash:
            continue
        if item.get("status") == "stale":
            continue
        refreshed = refresh_analysis_status(item, None, new_revision_id)
        _save_payload(conn, "app_chapter_analysis", scope, refreshed)
        invalidated.append(item["id"])
        for name, field in (("summary", "recap"), ("handoff", "next_brief")):
            section = item[name]
            if section.get("status") == "accepted":
                # Preserve subsequent author edits. The stale generated text
                # remains inspectable in the analysis history.
                conn.execute(
                    f"UPDATE app_chapter_meta SET {field}='',updated_at=? WHERE chapter_id=? AND {field}=?",
                    (_now(), chapter_id, section["text"]),
                )
    chapters, revisions = _current_sources(conn, scope)
    rows = conn.execute(
        "SELECT id FROM app_story_review WHERE project_id=? AND branch_id=?",
        (project_id, branch_id),
    ).fetchall()
    invalidated_reviews = []
    for row in rows:
        item = _row(conn, "app_story_review", scope, row[0])
        if item.get("status") != "stale" and not _review_current(item, chapters, revisions, conn=conn, scope=scope):
            item.update({"status": "stale", "review_required": True, "stale": True})
            for finding in item["findings"]:
                finding["stale"] = True
            _save_payload(conn, "app_story_review", scope, item)
            invalidated_reviews.append(item["id"])
    return {"analyses": invalidated, "reviews": invalidated_reviews, "chapter_id": chapter_id}


_EDITABLE = {"name", "note", "epistemic_mode", "known_by", "subject_name", "target_name", "predicate", "attribute", "value", "foreshadowing_stage", "valid_start", "valid_end", "story_time"}


def _validate_edited(
    candidate: Mapping[str, Any],
    edited: Mapping[str, Any] | None,
    chapter: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    require_knowledge: bool = True,
) -> dict[str, Any]:
    if edited is not None and (not isinstance(edited, Mapping) or set(edited) - _EDITABLE):
        raise ValueError("候选修改包含不可修改的来源字段。")
    if require_knowledge and candidate.get("known_by_unresolved") and "known_by" not in (edited or {}):
        raise ValueError("有知情人物尚未明确，请修改知情范围后再确认。")
    changed = {**candidate, **dict(edited or {})}
    checked = validate_chapter_analysis(
        {"summary": {"text": "", "evidence": []}, "handoff": {"text": "", "evidence": []}, "candidates": [changed]},
        chapter, project_id=scope["project_id"], branch_id=scope["branch_id"],
        revision_id=candidate["source_revision_id"], records=scope.get("records") or [],
    )["candidates"][0]
    checked["id"] = candidate["id"]
    checked["dedupe_key"] = candidate.get("dedupe_key", candidate["id"])
    if not require_knowledge and candidate.get("known_by_unresolved") and "known_by" not in (edited or {}):
        checked["known_by_unresolved"] = list(candidate["known_by_unresolved"])
        checked["entity_resolution"] = copy.deepcopy(candidate.get("entity_resolution", {}))
    return checked


def decide_analysis(
    conn: sqlite3.Connection,
    scope: Mapping[str, Any],
    analysis_id: str,
    data: Mapping[str, Any],
    callbacks: Mapping[str, Callable[..., Any]] | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    if not isinstance(data, Mapping):
        raise ValueError("分析确认内容格式错误。")
    callbacks = callbacks or {}
    with _atomic(conn):
        item = _row(conn, "app_chapter_analysis", scope, analysis_id)
        chapter, _, _ = _require_current(conn, scope, item["chapter_id"], item["source_revision_id"], item["chapter_hash"])
        _require_item_materials(conn, scope, item)
        _require_item_guidance(conn, scope, item, decision_source=True)
        if data.get("source_revision_id") != item["source_revision_id"] or data.get("chapter_hash") != item["chapter_hash"]:
            raise AnalysisConflict("确认页面对应的稿件版本已经改变，请刷新后重试。")
        decisions = data.get("decisions", [])
        if not isinstance(decisions, list) or len(decisions) > 120:
            raise ValueError("候选决定必须是列表。")
        candidate_map = {candidate["id"]: candidate for candidate in item["candidates"]}
        prior = {decision["candidate_id"]: decision for decision in item.get("decisions", [])}
        requested: set[str] = set()
        confirmed = []
        for decision in decisions:
            if not isinstance(decision, Mapping) or decision.get("decision") not in {"accepted", "rejected"}:
                raise ValueError("候选只能确认或拒绝。")
            candidate_id = str(decision.get("candidate_id") or "")
            if candidate_id in requested or candidate_id not in candidate_map:
                raise ValueError("候选编号重复或不存在。")
            requested.add(candidate_id)
            candidate = candidate_map[candidate_id]
            if candidate_id in prior:
                earlier = prior[candidate_id]
                if earlier["decision"] != decision["decision"]:
                    raise AnalysisConflict("此候选已经处理，撤回正式资料需使用资料修订流程。")
                if decision.get("edited") and any(candidate.get(key) != value for key, value in decision["edited"].items()):
                    raise AnalysisConflict("此候选已经处理，不能在重试时改变内容。")
                continue
            normalized = _validate_edited(
                candidate, decision.get("edited"), chapter, scope,
                require_knowledge=decision["decision"] == "accepted",
            )
            if decision["decision"] == "accepted":
                if normalized.get("known_by_unresolved"):
                    raise ValueError("有知情人物尚未明确，请修改知情范围后再确认。")
                apply = callbacks.get("apply_candidate")
                artifacts = apply(conn, scope, copy.deepcopy(normalized)) if apply else {}
                if artifacts is not None and not isinstance(artifacts, Mapping):
                    raise ValueError("candidate persistence callback must return artifact references")
                confirmed.append(copy.deepcopy(normalized))
                normalized["artifacts"] = dict(artifacts or {})
            normalized.update({
                "status": decision["decision"], "review_required": False,
                "reviewed_by": str(scope.get("actor_id") or "local-user"), "reviewed_at": _now(),
            })
            candidate.clear()
            candidate.update(normalized)
            saved_decision = {"candidate_id": candidate_id, "decision": decision["decision"], "at": normalized["reviewed_at"], "actor": normalized["reviewed_by"]}
            item["decisions"].append(saved_decision)
        for name, field in (("summary", "recap"), ("handoff", "next_brief")):
            flag = data.get("confirm_" + name, False)
            if not isinstance(flag, bool):
                raise ValueError("summary and handoff confirmation flags must be booleans")
            requested_decision = data.get(name + "_decision") or ("accepted" if flag else None)
            if requested_decision is None:
                continue
            if requested_decision not in {"accepted", "rejected"}:
                raise ValueError("摘要和交接只能确认或拒绝。")
            section = item[name]
            old = section.get("status")
            text = data.get(name + "_text", section["text"])
            if not isinstance(text, str) or len(text) > 20_000 or (requested_decision == "accepted" and not text.strip()):
                raise ValueError("摘要或交接文字不能为空或过长。")
            if old in {"accepted", "rejected"}:
                if old != requested_decision or text != section["text"]:
                    raise AnalysisConflict("摘要或交接已经处理，修改请重新分析或直接编辑章节资料。")
                continue
            if requested_decision == "accepted":
                for evidence in section["evidence"]:
                    validate_source_quote(evidence, chapter, item["source_revision_id"])
                cursor = conn.execute(f"UPDATE app_chapter_meta SET {field}=?,updated_at=? WHERE chapter_id=?", (text, _now(), item["chapter_id"]))
                if cursor.rowcount != 1:
                    raise ValueError("章节资料不存在，无法保存摘要或交接。")
            section.update({"text": text, "status": requested_decision, "reviewed_at": _now(), "reviewed_by": str(scope.get("actor_id") or "local-user")})
        unresolved = any(candidate["status"] == "candidate" for candidate in item["candidates"]) or any(item[name]["status"] == "pending" for name in ("summary", "handoff"))
        item["status"] = "pending" if unresolved else "reviewed"
        item["review_required"] = unresolved
        if item.get("material_source") is not None:
            item["decision_material_source"] = moyu_materials.refresh(
                conn, *_scope(scope), item["material_source"])
        if item.get("guidance_source") is not None:
            import moyu_workspace
            # Record only this transaction's author decisions; never replace
            # the guidance frozen with the original model request.
            item["decision_guidance_source"] = moyu_workspace.guidance_source(
                conn, *_scope(scope), item["chapter_id"])
        _save_payload(conn, "app_chapter_analysis", scope, item)
        notify = callbacks.get("record_decision")
        if notify:
            notify(conn, scope, copy.deepcopy(item))
        response = copy.deepcopy(item)
        response["confirmed_candidates"] = confirmed
        return response


@_transactional
def save_review(
    conn: sqlite3.Connection,
    scope: Mapping[str, Any],
    chapter: Mapping[str, Any],
    run_id: str | None,
    structured: Mapping[str, Any],
) -> dict[str, Any]:
    ensure_tables(conn)
    project_id, branch_id = _scope(scope)
    revision_id = str(chapter.get("source_revision_id") or chapter.get("revision_id") or structured.get("revision_id") or "")
    if not revision_id:
        raise ValueError("review requires its original source revision")
    digest = text_hash(str(chapter.get("body") or ""))
    _, chapters, revisions = _require_current(conn, scope, str(chapter["id"]), revision_id, digest)
    allowed_evidence = sent_evidence_ranges(scope["sent_context"]) if "sent_context" in scope else None
    validated = validate_story_review(
        structured, chapters, revisions, project_id=project_id, branch_id=branch_id,
        current_chapter_id=str(chapter["id"]),
        allowed_evidence=allowed_evidence,
    )
    source_ids = {str(chapter["id"])}
    for evidence in allowed_evidence or []:
        if evidence["source_start"] == evidence["source_end"]:
            continue
        source_id = evidence["source_chapter_id"]
        if source_id not in chapters:
            raise AnalysisConflict("审稿材料的来源章节已删除，请重新审稿。")
        try:
            validate_source_quote(evidence, chapters[source_id], revisions[source_id])
        except ValueError as exc:
            raise AnalysisConflict("审稿材料已经改变，请重新审稿。") from exc
        source_ids.add(source_id)
    for finding in validated["findings"]:
        source_ids.update(evidence["source_chapter_id"] for evidence in finding["evidence"])
    validated["source_dependencies"] = [
        {"chapter_id": source_id, "revision_id": revisions[source_id], "chapter_hash": text_hash(chapters[source_id]["body"])}
        for source_id in sorted(source_ids)
    ]
    sent_context = scope.get("sent_context") or {}
    if sent_context.get("_material_source") is None and _materials_required(conn, scope, run_id, sent_context):
        raise AnalysisConflict("审稿结果缺少完整资料记录，请按当前资料重新审稿。")
    guidance = sent_context.get("_guidance_source")
    if guidance is None and (sent_context.get("_material_source") is not None
                             or _materials_required(conn, scope, run_id, sent_context)):
        raise AnalysisConflict("审稿结果缺少完整创作指南记录，请按当前指南重新审稿。")
    if "_guidance_source" in sent_context:
        _require_guidance_source(conn, scope, str(chapter["id"]), guidance)
        validated["guidance_source"] = copy.deepcopy(dict(guidance))
    if "_material_source" in sent_context:
        marker = sent_context["_material_source"]
        _require_material_source(conn, scope, marker)
        validated["material_source"] = copy.deepcopy(marker)
    review_key = text_hash(_json({
        "project_id": project_id, "branch_id": branch_id, "chapter_id": chapter["id"],
        "revision_id": revision_id, "findings": validated["findings"], "uncertainties": validated["uncertainties"],
        "source_dependencies": validated["source_dependencies"], "guidance_source": validated.get("guidance_source"),
        "material_source": validated.get("material_source"),
    }))
    existing = conn.execute(
        "SELECT id FROM app_story_review WHERE project_id=? AND branch_id=? AND review_key=?",
        (project_id, branch_id, review_key),
    ).fetchone()
    if existing:
        return _row(conn, "app_story_review", scope, existing[0])
    review_id = "story_review_" + review_key[:28]
    validated.update({
        "id": review_id, "source_revision_id": revision_id, "chapter_hash": digest,
        "review_key": review_key, "run_id": run_id,
        "status": "pending_author_review" if validated["findings"] else "no_findings_unverified",
        "decision_log": [],
    })
    stamp = _now()
    conn.execute(
        "INSERT INTO app_story_review(id,project_id,branch_id,chapter_id,source_revision_id,chapter_hash,run_id,review_key,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (review_id, project_id, branch_id, chapter["id"], revision_id, digest, run_id, review_key, _json(validated), stamp, stamp),
    )
    # A new review replaces old evidence and undecided suggestions, while a
    # current issue the author accepted remains actionable until explicitly handled.
    for row in conn.execute(
        "SELECT id FROM app_story_review WHERE project_id=? AND branch_id=? AND chapter_id=? AND id<>?",
        (project_id, branch_id, chapter["id"], review_id),
    ).fetchall():
        prior = _row(conn, "app_story_review", scope, row[0])
        current = not prior.get("stale") and prior.get("status") != "stale" and _review_current(prior, chapters, revisions, conn=conn, scope=scope)
        if current and any(finding.get("status") == "needs_revision" for finding in prior["findings"]):
            continue
        prior["superseded_by"] = review_id
        _save_payload(conn, "app_story_review", scope, prior)
    return _row(conn, "app_story_review", scope, review_id)


def _review_current(
    item: Mapping[str, Any], chapters: Mapping[str, Mapping[str, Any]], revisions: Mapping[str, str],
    *, conn: sqlite3.Connection | None = None, scope: Mapping[str, Any] | None = None,
) -> bool:
    source = chapters.get(item["chapter_id"])
    if source is None or revisions.get(item["chapter_id"]) != item["source_revision_id"] or text_hash(source["body"]) != item["chapter_hash"]:
        return False
    for dependency in item.get("source_dependencies", []):
        chapter_id = dependency["chapter_id"]
        if (chapter_id not in chapters or revisions.get(chapter_id) != dependency["revision_id"]
                or text_hash(chapters[chapter_id]["body"]) != dependency["chapter_hash"]):
            return False
    if conn is not None and scope is not None:
        try:
            _require_item_materials(conn, scope, item)
            _require_item_guidance(conn, scope, item)
        except AnalysisConflict:
            return False
    elif item.get("material_source") is not None or item.get("guidance_source") is not None:
        return False
    for finding in item["findings"]:
        for evidence in finding["evidence"]:
            chapter_id = evidence["source_chapter_id"]
            if chapter_id not in chapters:
                return False
            try:
                validate_source_quote(evidence, chapters[chapter_id], revisions.get(chapter_id, ""))
            except ValueError:
                return False
    return True


def _resolution_source(
    conn: sqlite3.Connection, scope: Mapping[str, Any], item: Mapping[str, Any],
    chapters: Mapping[str, Mapping[str, Any]], revisions: Mapping[str, str],
) -> dict[str, Any] | None:
    chapter = chapters.get(item["chapter_id"])
    if chapter is None:
        return None
    project_id, branch_id = _scope(scope)
    source_ids = {dependency["chapter_id"] for dependency in item.get("source_dependencies", [])}
    source_ids.update(evidence["source_chapter_id"] for finding in item["findings"] for evidence in finding["evidence"])
    source = {
        "project_id": project_id, "branch_id": branch_id, "chapter_id": item["chapter_id"],
        "source_revision_id": revisions[item["chapter_id"]], "chapter_hash": text_hash(chapter["body"]),
        "evidence_sources": [
            {"chapter_id": source_id, "revision_id": revisions.get(source_id),
             "chapter_hash": text_hash(chapters[source_id]["body"]) if source_id in chapters else None}
            for source_id in sorted(source_ids)
        ],
    }
    if _item_sources_required(conn, scope, item):
        import moyu_workspace
        source["guidance_source"] = moyu_workspace.guidance_source(conn, project_id, branch_id, item["chapter_id"])
    if item.get("material_source") is not None:
        try:
            source["material_source"] = moyu_materials.author_resolution_materials(
                conn, project_id, branch_id, item["material_source"])
        except ValueError:
            return None
    elif _item_sources_required(conn, scope, item):
        source["legacy_material_source"] = moyu_materials.author_resolution_snapshot(
            conn, project_id, branch_id)
    return source


def _annotate_review(
    conn: sqlite3.Connection, scope: Mapping[str, Any], item: dict[str, Any],
    chapters: Mapping[str, Mapping[str, Any]], revisions: Mapping[str, str],
) -> dict[str, Any]:
    current = not item.get("stale") and item.get("status") != "stale" and _review_current(item, chapters, revisions, conn=conn, scope=scope)
    if not current:
        item.update({"status": "stale", "review_required": True, "stale": True})
    source = _resolution_source(conn, scope, item, chapters, revisions)
    item["author_resolution_source"] = source
    for finding in item["findings"]:
        if not current:
            finding["stale"] = True
        disposition = finding.get("stale_resolution") or finding.get("author_resolution") or {}
        finding["author_resolution_current"] = bool(
            source and disposition.get("source") == source
            and disposition.get("decision") in {"ignored", "explained"}
            and str(disposition.get("reason") or "").strip()
        )
    return item


def review_publication_gate(
    conn: sqlite3.Connection, scope: Mapping[str, Any], chapter_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Consume saved author decisions without requesting a model or reusing old evidence."""
    ensure_tables(conn)
    project_id, branch_id = _scope(scope)
    chapters, revisions = _current_sources(conn, scope)
    rows = conn.execute(
        "SELECT id,chapter_id,rowid FROM app_story_review WHERE project_id=? AND branch_id=? ORDER BY rowid DESC",
        (project_id, branch_id),
    ).fetchall()
    sequence = {row[0]: row[2] for row in rows}
    issues, dependencies = [], []
    for row in rows:
        if row[1] not in chapters or (chapter_id is not None and row[1] != chapter_id):
            continue
        item = _annotate_review(conn, scope, _row(conn, "app_story_review", scope, row[0]), chapters, revisions)
        successor = item.get("superseded_by")
        superseded = bool(successor in sequence and sequence[successor] > row[2])
        dependencies.append({
            "id": item["id"], "chapter_id": item["chapter_id"], "status": item["status"],
            "stale": bool(item.get("stale")), "superseded_by": successor if superseded else None,
            "findings": [
                {"id": finding["id"], "status": finding["status"],
                 "author_resolution_current": finding["author_resolution_current"]}
                for finding in item["findings"]
            ],
        })
        if superseded:
            continue
        if item.get("stale"):
            unresolved = [finding for finding in item["findings"] if not finding["author_resolution_current"]]
            code = "semantic_review_stale"
            message = "故事审稿依据已改变，请重新审稿，或结合当前稿件逐项说明忽略/解释理由。"
        else:
            unresolved = [finding for finding in item["findings"] if finding.get("status") in {"pending", "needs_revision"}]
            needs_revision = any(finding.get("status") == "needs_revision" for finding in unresolved)
            code = "semantic_review_needs_revision" if needs_revision else "semantic_review_pending"
            message = "故事审稿仍有作者确认需要修订的问题，请改稿后重新审稿，或说明解释/忽略理由。" if needs_revision else "故事审稿仍有待作者处理的问题，请逐项决定并说明理由。"
        if unresolved:
            issues.append({
                "code": code, "message": message, "review_id": item["id"],
                "chapter_id": item["chapter_id"], "issue_ids": [finding["id"] for finding in unresolved],
            })
    return {"issues": issues, "dependencies": dependencies}


def _review_repair_target(
    scope: Mapping[str, Any],
    evidence: Mapping[str, Any],
    chapters: Mapping[str, Mapping[str, Any]],
    revisions: Mapping[str, str],
    evidence_index: int,
) -> dict[str, Any]:
    project_id, branch_id = _scope(scope)
    chapter_id = evidence["source_chapter_id"]
    chapter = chapters.get(chapter_id)
    if chapter is None:
        raise AnalysisConflict("审稿原句的来源章节已不存在，请重新审稿。")
    checked = validate_source_quote(evidence, chapter, revisions.get(chapter_id, ""))
    return {
        "evidence_index": evidence_index,
        "chapter_title": chapter["title"],
        "source": {
            "project_id": project_id, "branch_id": branch_id, "chapter_id": chapter_id,
            "source_revision_id": checked["source_revision_id"],
            "source_body_hash": checked["chapter_hash"], "mode": "replace",
            "range": {
                "start": checked["source_start"], "end": checked["source_end"],
                "quote": checked["source_quote"],
            },
        },
    }


def review_repair_source(
    conn: sqlite3.Connection,
    scope: Mapping[str, Any],
    review_source: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a local repair to one explicit passage in a current scoped review."""
    ensure_tables(conn)
    if not isinstance(review_source, Mapping):
        raise ValueError("审稿修订来源格式错误。")
    review_id, issue_id = review_source.get("review_id"), review_source.get("issue_id")
    evidence_index = review_source.get("evidence_index")
    if (not isinstance(review_id, str) or not review_id or len(review_id) > 200
            or not isinstance(issue_id, str) or not issue_id or len(issue_id) > 200):
        raise ValueError("请选择保存的审稿问题。")
    if isinstance(evidence_index, bool) or not isinstance(evidence_index, int) or evidence_index < 0:
        raise ValueError("请明确选择要修订的原句。")
    item = _row(conn, "app_story_review", scope, review_id)
    chapters, revisions = _current_sources(conn, scope)
    if item.get("stale") or item.get("status") == "stale" or not _review_current(item, chapters, revisions, conn=conn, scope=scope):
        raise AnalysisConflict("审稿依据已经改变，请重新检查当前稿件。")
    finding = next((finding for finding in item["findings"] if finding["id"] == issue_id), None)
    if finding is None:
        raise ValueError("审稿问题不存在。")
    if evidence_index >= len(finding["evidence"]):
        raise ValueError("所选原句不属于这条审稿问题。")
    target = _review_repair_target(scope, finding["evidence"][evidence_index], chapters, revisions, evidence_index)
    return {
        "source": target["source"],
        "review_source": {"review_id": review_id, "issue_id": issue_id, "evidence_index": evidence_index},
        "issue": {
            "message": finding["message"], "suggestion": finding.get("suggestion", ""),
            "evidence": copy.deepcopy(finding["evidence"]),
        },
    }


def list_reviews(conn: sqlite3.Connection, scope: Mapping[str, Any], chapter_id: str | None = None) -> list[dict[str, Any]]:
    ensure_tables(conn)
    project_id, branch_id = _scope(scope)
    chapters, revisions = _current_sources(conn, scope)
    clause = "project_id=? AND branch_id=?"
    params: list[Any] = [project_id, branch_id]
    if chapter_id:
        clause += " AND chapter_id=?"
        params.append(chapter_id)
    rows = conn.execute("SELECT id FROM app_story_review WHERE " + clause + " ORDER BY rowid DESC", params).fetchall()
    result = []
    for row in rows:
        item = _annotate_review(conn, scope, _row(conn, "app_story_review", scope, row[0]), chapters, revisions)
        current = not item.get("stale")
        for finding in item["findings"]:
            finding["repair_targets"] = [
                _review_repair_target(scope, evidence, chapters, revisions, index)
                for index, evidence in enumerate(finding["evidence"])
            ] if current else []
        result.append(item)
    blocked = {issue["review_id"] for issue in review_publication_gate(conn, scope, chapter_id)["issues"]}
    result.sort(key=lambda item: item["id"] not in blocked)
    return result


def decide_review(
    conn: sqlite3.Connection,
    scope: Mapping[str, Any],
    review_id: str,
    issue_id: str,
    data: Mapping[str, Any],
    callbacks: Mapping[str, Callable[..., Any]] | None = None,
) -> dict[str, Any]:
    ensure_tables(conn)
    callbacks = callbacks or {}
    if not isinstance(data, Mapping) or data.get("decision") not in {"accepted", "ignored", "explained"}:
        raise ValueError("请选择准备修订、忽略或已解释。")
    reason = data.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 3000 or (data["decision"] in {"ignored", "explained"} and not reason.strip()):
        raise ValueError("忽略或解释问题时请说明理由。")
    with _atomic(conn):
        item = _row(conn, "app_story_review", scope, review_id)
        chapters, revisions = _current_sources(conn, scope)
        current = not item.get("stale") and item.get("status") != "stale" and _review_current(item, chapters, revisions, conn=conn, scope=scope)
        issue = next((finding for finding in item["findings"] if finding["id"] == issue_id), None)
        if issue is None:
            raise ValueError("审稿问题不存在。")
        if not current:
            source = _resolution_source(conn, scope, item, chapters, revisions)
            if data["decision"] == "accepted" or source is None or data.get("resolution_source") != source:
                raise AnalysisConflict("审稿依据已经改变，请重新审稿，或按当前稿件明确说明忽略/解释理由。")
            disposition = {
                "decision": data["decision"], "reason": reason, "source": source,
                "actor": str(scope.get("actor_id") or "local-user"),
            }
            previous = issue.get("stale_resolution") or {}
            if all(previous.get(key) == value for key, value in disposition.items()):
                return _annotate_review(conn, scope, copy.deepcopy(item), chapters, revisions)
            disposition["at"] = _now()
            issue["stale_resolution"] = disposition
            item.update({"status": "stale", "stale": True})
            item["decision_log"].append({
                "issue_id": issue_id, **copy.deepcopy(disposition), "stale_evidence": True,
                "previous_decision": issue.get("decision"),
            })
            _save_payload(conn, "app_story_review", scope, item)
            notify = callbacks.get("record_decision")
            if notify:
                notify(conn, scope, copy.deepcopy(item))
            return _annotate_review(conn, scope, copy.deepcopy(item), chapters, revisions)
        prior = issue.get("decision")
        if prior:
            if prior == data["decision"] and issue.get("reason", "") == reason:
                return copy.deepcopy(item)
            if prior != "accepted" or data["decision"] not in {"ignored", "explained"}:
                raise AnalysisConflict("问题已处理，重新判断请重新审稿。")
        issue.update({
            "decision": data["decision"], "reason": reason,
            "status": "needs_revision" if data["decision"] == "accepted" else data["decision"],
            "reviewed_by": str(scope.get("actor_id") or "local-user"), "reviewed_at": _now(),
        })
        if data["decision"] in {"ignored", "explained"}:
            source = _resolution_source(conn, scope, item, chapters, revisions)
            if source is None:
                raise AnalysisConflict("当前稿件资料无法核对，请重新打开审稿后说明理由。")
            issue["author_resolution"] = {
                "decision": data["decision"], "reason": reason, "source": source,
                "actor": issue["reviewed_by"], "at": issue["reviewed_at"],
            }
        item["decision_log"].append({"issue_id": issue_id, "decision": data["decision"], "reason": reason, "at": issue["reviewed_at"], "actor": issue["reviewed_by"], "previous_decision": prior})
        statuses = {finding["status"] for finding in item["findings"]}
        item["status"] = "needs_revision" if "needs_revision" in statuses else "pending_author_review" if "pending" in statuses else "resolved_by_author"
        item["review_required"] = item["status"] != "resolved_by_author"
        _save_payload(conn, "app_story_review", scope, item)
        notify = callbacks.get("record_decision")
        if notify:
            notify(conn, scope, copy.deepcopy(item))
        return copy.deepcopy(item)
