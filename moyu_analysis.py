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
import sqlite3
import uuid
from typing import Any, Callable, Mapping

from moyu_context import (
    refresh_analysis_status, sent_evidence_ranges, text_hash, validate_chapter_analysis,
    validate_source_quote, validate_story_review,
)


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
    return [
        refresh_analysis_status(item, chapters.get(item["chapter_id"]), revisions.get(item["chapter_id"], ""))
        for row in rows for item in [_row(conn, "app_chapter_analysis", scope, row[0])]
    ]


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
        if item.get("status") != "stale" and not _review_current(item, chapters, revisions):
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
    validated = validate_story_review(
        structured, chapters, revisions, project_id=project_id, branch_id=branch_id,
        current_chapter_id=str(chapter["id"]),
        allowed_evidence=sent_evidence_ranges(scope["sent_context"]) if "sent_context" in scope else None,
    )
    review_key = text_hash(_json({"project_id": project_id, "branch_id": branch_id, "chapter_id": chapter["id"], "revision_id": revision_id, "findings": validated["findings"], "uncertainties": validated["uncertainties"]}))
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
    return _row(conn, "app_story_review", scope, review_id)


def _review_current(item: Mapping[str, Any], chapters: Mapping[str, Mapping[str, Any]], revisions: Mapping[str, str]) -> bool:
    source = chapters.get(item["chapter_id"])
    if source is None or revisions.get(item["chapter_id"]) != item["source_revision_id"] or text_hash(source["body"]) != item["chapter_hash"]:
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
    if item.get("stale") or item.get("status") == "stale" or not _review_current(item, chapters, revisions):
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
    rows = conn.execute("SELECT id FROM app_story_review WHERE " + clause + " ORDER BY updated_at DESC,id", params).fetchall()
    result = []
    for row in rows:
        item = _row(conn, "app_story_review", scope, row[0])
        current = not item.get("stale") and item.get("status") != "stale" and _review_current(item, chapters, revisions)
        if not current:
            item.update({"status": "stale", "review_required": True, "stale": True})
            for finding in item["findings"]:
                finding["stale"] = True
        for finding in item["findings"]:
            finding["repair_targets"] = [
                _review_repair_target(scope, evidence, chapters, revisions, index)
                for index, evidence in enumerate(finding["evidence"])
            ] if current else []
        result.append(item)
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
        _, chapters, revisions = _require_current(conn, scope, item["chapter_id"], item["source_revision_id"], item["chapter_hash"])
        if not _review_current(item, chapters, revisions):
            raise AnalysisConflict("审稿依据已经改变，请重新检查当前稿件。")
        issue = next((finding for finding in item["findings"] if finding["id"] == issue_id), None)
        if issue is None:
            raise ValueError("审稿问题不存在。")
        prior = issue.get("decision")
        if prior:
            if prior != data["decision"] or issue.get("reason", "") != reason:
                raise AnalysisConflict("问题已处理，重新判断请重新审稿。")
            return copy.deepcopy(item)
        issue.update({
            "decision": data["decision"], "reason": reason,
            "status": "needs_revision" if data["decision"] == "accepted" else data["decision"],
            "reviewed_by": str(scope.get("actor_id") or "local-user"), "reviewed_at": _now(),
        })
        item["decision_log"].append({"issue_id": issue_id, "decision": data["decision"], "reason": reason, "at": issue["reviewed_at"], "actor": issue["reviewed_by"]})
        statuses = {finding["status"] for finding in item["findings"]}
        item["status"] = "needs_revision" if "needs_revision" in statuses else "pending_author_review" if "pending" in statuses else "resolved_by_author"
        item["review_required"] = item["status"] != "resolved_by_author"
        _save_payload(conn, "app_story_review", scope, item)
        notify = callbacks.get("record_decision")
        if notify:
            notify(conn, scope, copy.deepcopy(item))
        return copy.deepcopy(item)
