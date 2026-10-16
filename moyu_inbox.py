"""Read-only, scope-bound author inbox with counts before pagination."""
from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import uuid

import moyu_analysis
from moyu_context import source_evidence_reasons, text_hash
import moyu_materials
import moyu_workspace


KINDS = ("agent", "queue", "analysis", "review")
_AGENT_WAITING = {
    "awaiting_authorization": "等待授权",
    "awaiting_adoption": "等待采用正文",
    "awaiting_plan_confirmation": "等待确认规划",
    "awaiting_plan_application": "等待采用规划",
    "requires_action": "需要作者处理",
}
_TASK_LABELS = {
    "continue": "续写候选", "polish": "修订候选", "chapter_analysis": "章后记忆",
    "semantic_review": "故事审稿", "outline": "大纲候选", "scene": "场景候选",
    "brainstorm": "走向候选", "summary": "总结候选", "check": "连续性检查",
}


@contextmanager
def _snapshot(conn):
    # SAVEPOINT preserves an existing caller transaction, including on failure.
    name = "writing_inbox_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + name)
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO " + name)
        conn.execute("RELEASE " + name)
        raise
    else:
        conn.execute("RELEASE " + name)


def _require_schema(conn):
    required = {
        ("table", "app_chapter_analysis"), ("table", "app_story_review"),
        ("index", "app_chapter_analysis_scope"), ("index", "app_story_review_scope"),
    }
    present = {(row[0], row[1]) for row in conn.execute(
        "SELECT type,name FROM sqlite_master WHERE name IN "
        "('app_chapter_analysis','app_story_review',"
        "'app_chapter_analysis_scope','app_story_review_scope')"
    )}
    if not required <= present:
        # Existing analysis readers use CREATE IF NOT EXISTS. Never let this
        # endpoint initialize or repair a database while reading its inbox.
        raise sqlite3.OperationalError("写作待办暂不可用，请重新启动应用完成数据库检查。")


def _object(raw):
    value = json.loads(raw or "{}")
    if not isinstance(value, dict):
        raise ValueError("写作待办记录损坏，请检查本地任务记录。")
    return value


def _item(kind, ref, chapter, title, status, label, updated_at, *, stale=False,
          read_only=False, priority=2, **extra):
    return {
        "id": kind + ":" + ref, "kind": kind, "ref_id": ref,
        "chapter_id": chapter["id"] if chapter else None,
        "chapter_title": chapter["title"] if chapter else "",
        "title": title, "status": status, "status_label": label,
        "stale": bool(stale), "read_only": bool(read_only),
        "updated_at": updated_at or "", "priority": priority,
        **extra,
    }


def _source_current(source, task, chapter, revisions, project, branch):
    if not isinstance(source, dict) or chapter is None:
        return False
    body = chapter["body"]
    expected = {
        "project_id": project, "branch_id": branch, "chapter_id": chapter["id"],
        "source_revision_id": revisions[chapter["id"]], "source_body_hash": text_hash(body),
    }
    if any(source.get(key) and source[key] != value for key, value in expected.items()):
        return False
    # An unbound legacy result is readable, never a current candidate.
    if not source.get("source_revision_id") or not source.get("source_body_hash"):
        return False
    mode = "append" if task == "continue" else "replace" if task == "polish" else "structure"
    if source.get("mode", mode) not in (mode, "structured" if mode == "structure" else mode):
        return False
    selected = source.get("range")
    if not isinstance(selected, dict):
        return False
    start, end = selected.get("start"), selected.get("end")
    if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(body)
            or selected.get("quote", body[start:end]) != body[start:end]):
        return False
    return not ((mode == "append" and (start != len(body) or end != len(body)))
                or (mode == "replace" and start == end))


def _job_sources_current(conn, row, context, consent, project, branch):
    """Use the same frozen-reference/material/guidance checks as queue detail."""
    if not row["run_id"]:
        return False
    try:
        moyu_materials.validate_frozen_reference_context(
            context, consent.get("request_audit"),
            input_hash=row["input_hash"], prompt_hash=row["prompt_hash"])
        if context.get("reference_passages") and (
                row["task"] not in {"continue", "polish"} or consent.get("task") != row["task"]):
            return False
        moyu_materials.validate_reference_context(conn, project, branch, context)
        if not (context.get("planning_workflow") and "_material_source" not in context):
            moyu_materials.validate(conn, project, branch, context.get("_material_source"))
        guidance = context.get("_guidance_source")
        if guidance is not None and (
                not isinstance(guidance, dict)
                or guidance != moyu_workspace.guidance_source(conn, project, branch, guidance.get("chapter_id"))):
            return False
    except ValueError:
        return False
    return True


def _result_read_only(row):
    result = _object(row["result_json"])
    return (row["run_status"] != "success" or row["status"] != "success" or row["cancel_requested"]
            or not result.get("safe_to_apply") or result.get("truncated")
            or result.get("completion_state") not in {"complete", "offline_complete"})


def _authorization_issue(row, plan, context_reader):
    expected = plan.get("context_hash")
    if (not isinstance(expected, str) or len(expected) != 64
            or any(character not in "0123456789abcdef" for character in expected)):
        return "旧任务缺少完整创作依据，请按当前资料重新查看计划。"
    if context_reader is None:
        return "本次计划的创作依据尚未核实，请连接本机服务后重新读取待办。"
    mode = plan.get("mode", "inspect")
    task = mode if mode in {"continue", "polish"} else "chapter_analysis"
    source = plan.get("source") or {}
    selection = ({
        "range": source.get("range"), "revision_id": source.get("source_revision_id"),
        "chapter_hash": source.get("source_body_hash"),
    } if mode == "polish" else None)
    try:
        # The caller supplies the same context builder used at authorization.
        # The enclosing SAVEPOINT keeps it in this inbox's read snapshot.
        context = context_reader(
            row["chapter_id"], instruction=row["query"], task=task, selection=selection)
    except ValueError as exc:
        return str(exc) or "本次计划的创作依据无法核实，请按当前资料重新查看计划。"
    if not isinstance(context, dict):
        raise ValueError("写作计划依据格式不完整，请重新读取待办。")
    current = text_hash(json.dumps(context, ensure_ascii=False, separators=(",", ":")))
    return "" if current == expected else "创作资料已变化，请按当前资料重新查看计划。"


def _agents(conn, scope, chapters, revisions, jobs, authorization_context=None):
    items, owned = [], set()
    for row in conn.execute(
        "SELECT * FROM app_agent_session WHERE project_id=? AND branch_id=? ORDER BY updated_at DESC,id",
        (scope["project_id"], scope["branch_id"]),
    ):
        if row["restored_read_only"] or row["status"] in {"review_ready", "canceled", "restored"}:
            continue
        plan = _object(row["plan_json"])
        summary = plan.get("summary") or {}
        job_id = summary.get("current_job_id")
        job = jobs.get(job_id)
        status = row["status"]
        if status == "awaiting_job" and job and (
                job["status"] in {"failed", "unknown", "canceled"} or job["run_status"] == "unknown"):
            status = "requires_action"
        if status not in _AGENT_WAITING:
            continue
        chapter = chapters.get(row["chapter_id"])
        if plan.get("mode") == "plan":
            current = moyu_workspace.blueprint_row(conn, scope["project_id"], scope["branch_id"])
            source = {
                "blueprint_version": current["version"],
                "blueprint_hash": text_hash(json.dumps(
                    {"version": current["version"], "blueprint": current["blueprint"]},
                    ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)),
            }
            stale = source != plan.get("applied_source", plan.get("source"))
        else:
            source = plan.get("adopted_source", plan.get("source")) or {}
            stale = chapter is None or revisions.get(row["chapter_id"]) != source.get("source_revision_id")
        authorization_issue = ""
        if (status == "awaiting_authorization" and plan.get("mode") != "plan" and not stale):
            authorization_issue = _authorization_issue(row, plan, authorization_context)
            stale = bool(authorization_issue)
        if job and status == "awaiting_adoption":
            context, consent = _object(job["context_json"]), _object(job["consent_json"])
            stale |= not _job_sources_current(
                conn, job, context, consent, scope["project_id"], scope["branch_id"])
            stale |= not _source_current(
                context.get("_source"), job["task"], chapter, revisions,
                scope["project_id"], scope["branch_id"])
        candidate_read_only = bool(job and status == "awaiting_adoption" and _result_read_only(job))
        label = "来源已改变，需重新检查" if stale else (
            "候选仅供阅读" if candidate_read_only else _AGENT_WAITING[status])
        items.append(_item(
            "agent", row["id"], chapter,
            "全书规划" if plan.get("mode") == "plan" else "写作总管",
            status, label, row["updated_at"], stale=stale, read_only=stale or candidate_read_only,
            priority=1 if status == "requires_action" else 2,
            action="open_agent", session_id=row["id"], job_id=job_id or None,
            mode=plan.get("mode", "inspect"),
            **({"reason": authorization_issue} if authorization_issue else {}),
        ))
        if job_id in jobs:
            owned.add(job_id)
    return items, owned


def _queue(conn, scope, chapters, revisions, jobs, owned):
    items = []
    project, branch = scope["project_id"], scope["branch_id"]
    for row in jobs.values():
        if (row["task"] == "assistant_chat" or row["id"] in owned
                or row["status"] in {"queued", "running", "canceled"}):
            continue
        context, consent = _object(row["context_json"]), _object(row["consent_json"])
        if (context.get("_restored_read_only") or consent.get("restored_read_only")
                or row["applied"] or row["decided"]
                or row["author_decision"] not in (None, "", "pending")):
            continue
        unknown = row["status"] == "unknown" or row["run_status"] == "unknown"
        failed = row["status"] == "failed"
        if not unknown and not failed and row["status"] != "success":
            continue
        result = _object(row["result_json"])
        if not failed and not unknown and row["task"] in {"chapter_analysis", "semantic_review"}:
            key = "analysis" if row["task"] == "chapter_analysis" else "story_review"
            if isinstance(result.get(key), dict) and result[key].get("id"):
                continue
        chapter = chapters.get(row["chapter_id"])
        stale = not _job_sources_current(conn, row, context, consent, project, branch)
        if row["task"] in {"continue", "polish", "chapter_analysis", "semantic_review"}:
            stale |= not _source_current(
                context.get("_source"), row["task"], chapter, revisions, project, branch)
        status = "unknown" if unknown else "failed" if failed else "candidate_ready"
        label = "费用状态未知，需检查" if unknown else "任务失败，需检查" if failed else "等待查看候选"
        result_read_only = _result_read_only(row)
        if stale and not failed and not unknown:
            status, label = "stale", "来源已改变，仅供阅读"
        elif result_read_only and not failed and not unknown:
            status, label = "read_only", "结果仅供阅读"
        items.append(_item(
            "queue", row["id"], chapter, _TASK_LABELS.get(row["task"], "写作候选"),
            status, label, row["updated_at"], stale=stale,
            read_only=stale or result_read_only, priority=1 if failed or unknown else 2,
            action="open_job", job_id=row["id"], ai_run_id=row["ai_run_id"], task=row["task"],
        ))
    return items


def _analyses(conn, scope, chapters):
    items, represented_memory = [], set()
    for analysis in moyu_analysis.list_analysis(conn, scope):
        if analysis["chapter_id"] not in chapters:
            continue
        candidates = analysis.get("candidates") or []
        represented_memory.update(
            candidate["artifacts"]["memory_id"] for candidate in candidates
            if isinstance(candidate.get("artifacts"), dict) and candidate["artifacts"].get("memory_id"))
        decided = {decision.get("candidate_id") for decision in analysis.get("decisions", [])}
        unresolved = sum(candidate.get("status") in {"candidate", "stale"}
                         and candidate.get("id") not in decided for candidate in candidates)
        unresolved += sum((analysis.get(name) or {}).get("status") == "pending" for name in ("summary", "handoff"))
        if analysis.get("status") == "reviewed" or not unresolved:
            continue
        stale = analysis.get("stale") or analysis.get("status") == "stale"
        items.append(_item(
            "analysis", analysis["id"], chapters[analysis["chapter_id"]], "章后记忆待确认",
            "stale" if stale else "pending", "旧分析仅供阅读" if stale else "等待确认记忆",
            analysis["updated_at"], stale=stale, read_only=stale,
            action="open_analysis", analysis_id=analysis["id"], pending_count=unresolved,
        ))
    project, branch = scope["project_id"], scope["branch_id"]
    for row in conn.execute(
        "SELECT m.id,m.status,m.payload_json,m.created_at,m.source_span_id,"
        "s.project_id source_project,s.document_node_id,s.revision_id,s.status span_status,"
        "s.start_codepoint,s.end_codepoint,s.start_utf8,s.end_utf8,s.quote,s.quote_hash "
        "FROM candidate_memory m LEFT JOIN source_span s ON s.id=m.source_span_id "
        "WHERE m.project_id=? AND m.branch_id=? AND m.status IN ('candidate','stale')",
        (project, branch),
    ):
        if row["id"] in represented_memory:
            continue
        payload = _object(row["payload_json"])
        chapter = chapters.get(row["document_node_id"])
        if chapter is None and payload.get("source_chapter_id") in chapters:
            chapter = chapters[payload["source_chapter_id"]]
        stale = (row["status"] == "stale" or chapter is None or row["source_project"] != project
                 or row["span_status"] != "valid")
        if chapter is not None and not stale:
            current = conn.execute(
                "SELECT id FROM document_revision WHERE document_node_id=? AND branch_id=? ORDER BY rowid DESC LIMIT 1",
                (chapter["id"], branch),
            ).fetchone()
            stale = current is None or current[0] != row["revision_id"]
            if not stale:
                stale = bool(source_evidence_reasons({
                    **dict(row), "id": row["source_span_id"], "current_revision_id": current[0],
                }, chapter["body"], payload))
        items.append(_item(
            "analysis", row["id"], chapter, "独立记忆待确认",
            "stale" if stale else "pending", "来源失效，仅供阅读" if stale else "等待确认记忆",
            row["created_at"], stale=stale, read_only=stale,
            action="open_memory", memory_id=row["id"], pending_count=1,
        ))
    return items


def _reviews(conn, scope, chapters):
    gate = moyu_analysis.review_publication_gate(conn, scope)
    blocked, active = {}, {}
    for issue in gate["issues"]:
        blocked.setdefault(issue["chapter_id"], []).append(issue)
    for dependency in gate["dependencies"]:
        if not dependency.get("superseded_by"):
            active.setdefault(dependency["chapter_id"], []).append(dependency)
    stamps = {row["id"]: row["updated_at"] for row in conn.execute(
        "SELECT id,updated_at FROM app_story_review WHERE project_id=? AND branch_id=?",
        (scope["project_id"], scope["branch_id"]),
    )}
    items = []
    for chapter in chapters.values():
        if not chapter["body"].strip():
            continue
        issues, reports = blocked.get(chapter["id"], []), active.get(chapter["id"], [])
        if issues:
            codes = {issue["code"] for issue in issues}
            status = ("needs_revision" if "semantic_review_needs_revision" in codes else
                      "stale" if "semantic_review_stale" in codes else "pending_author_review")
            label = {"needs_revision": "已确认问题仍需修订", "stale": "审稿来源已改变",
                     "pending_author_review": "等待处理审稿问题"}[status]
            review_ids = [issue["review_id"] for issue in issues]
        else:
            # No findings is not a literary or publication pass. A stale report
            # with current explicit author resolutions follows the existing gate.
            unverified = [report for report in reports if not report["findings"]]
            if reports and not unverified:
                continue
            status = "stale" if any(report["stale"] for report in unverified) else (
                "no_findings_unverified" if unverified else "unreviewed")
            label = {"stale": "旧审稿需重新检查", "no_findings_unverified": "未发现问题，仍待作者检查",
                     "unreviewed": "本章尚未审稿"}[status]
            review_ids = [report["id"] for report in unverified]
        items.append(_item(
            "review", chapter["id"], chapter, "章节审稿",
            status, label, max((stamps[ref] for ref in review_ids), default=""),
            stale=status == "stale", priority=1 if status == "needs_revision" else 3,
            action="open_review", review_id=review_ids[0] if review_ids else None,
            review_ids=review_ids, pending_count=sum(len(issue["issue_ids"]) for issue in issues),
        ))
    return items


def writing_inbox(conn, work_id, branch_id, limit=25, offset=0, kind="all", *,
                  authorization_context=None):
    """Return full-scope counts and a stable page from the same read snapshot.

    Database/schema/decoding failures propagate to the API error handler. They
    must never masquerade as an empty, successfully read inbox. The optional
    authorization_context reader must rebuild the first task's context without
    writing content; without it, pending authorizations stay unverified.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("每页待办数量须为 1 至 100。")
    if type(offset) is not int or not 0 <= offset <= 2_147_483_647:
        raise ValueError("待办分页位置无效。")
    if not isinstance(kind, str) or kind not in ("all", *KINDS):
        raise ValueError("待办分类无效。")
    moyu_workspace.validate_id(work_id, "project id")
    moyu_workspace.validate_id(branch_id, "branch id")
    with _snapshot(conn):
        _require_schema(conn)
        moyu_workspace.resolve_scope(conn, work_id, branch_id)
        scope = {"project_id": work_id, "branch_id": branch_id}
        chapters, revisions = moyu_analysis._current_sources(conn, scope)
        jobs = {row["id"]: row for row in conn.execute(
            "SELECT j.*,r.id run_id,r.status run_status,r.author_decision,r.consent_json,"
            "r.input_hash,r.prompt_hash,"
            "EXISTS(SELECT 1 FROM app_candidate_application a WHERE a.ai_run_id=j.ai_run_id "
            "AND a.project_id=j.project_id AND a.branch_id=j.branch_id) applied,"
            "EXISTS(SELECT 1 FROM app_ai_run_decision d WHERE d.ai_run_id=j.ai_run_id "
            "AND d.project_id=j.project_id AND d.branch_id=j.branch_id) decided "
            "FROM app_ai_job j LEFT JOIN ai_run r ON r.id=j.ai_run_id "
            "AND r.project_id=j.project_id AND r.branch_id=j.branch_id "
            "WHERE j.project_id=? AND j.branch_id=? ORDER BY j.updated_at DESC,j.id",
            (work_id, branch_id),
        )}
        items, owned = _agents(
            conn, scope, chapters, revisions, jobs, authorization_context=authorization_context)
        items.extend(_queue(conn, scope, chapters, revisions, jobs, owned))
        items.extend(_analyses(conn, scope, chapters))
        items.extend(_reviews(conn, scope, chapters))
        counts = {category: sum(item["kind"] == category for item in items) for category in KINDS}
        filtered = [item for item in items if kind == "all" or item["kind"] == kind]
        filtered.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
        filtered.sort(key=lambda item: item["priority"])
        return {
            "project_id": work_id, "branch_id": branch_id, "read_only": True,
            "kind": kind, "counts": counts, "total": len(filtered), "total_all": len(items),
            "limit": limit, "offset": offset, "has_more": offset + limit < len(filtered),
            "items": filtered[offset:offset + limit],
        }
