"""Archive one source-bound chapter and open its successor without model calls."""
from __future__ import annotations

from contextlib import contextmanager
import json
import re
import uuid


SOURCE_VERSION = 1
SOURCE_FIELDS = frozenset({
    "version", "project_id", "branch_id", "chapter_id", "source_revision_id",
    "source_body_hash", "chapter_metadata_hash", "branch_head", "catalogue_hash",
    "blueprint_version", "blueprint_hash", "review_fingerprint", "commits_hash",
    "book_status", "project_archived", "branch_status",
})
META_FIELDS = (
    "title", "status", "chapter_goal", "scene_plan", "pov", "foreshadowing_op",
    "hook", "recap", "next_brief", "volume_id", "plan_id", "scenes_json",
)
EVENT_TYPE = "chapter.transition_completed"


class TransitionConflict(ValueError):
    """The author must discuss the changed source before confirming again."""


class TransitionBlocked(ValueError):
    """The current chapter is not ready to be archived."""


def _hash(api, value):
    return api.hash_text(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _catalogue(api, conn):
    return [dict(row) for row in conn.execute(
        "SELECT n.id,n.title,n.ordinal,r.id revision_id,r.content_hash,"
        "COALESCE(m.status,'草稿') status,COALESCE(m.chapter_goal,'') chapter_goal,"
        "COALESCE(m.scene_plan,'') scene_plan,COALESCE(m.pov,'') pov,"
        "COALESCE(m.foreshadowing_op,'') foreshadowing_op,COALESCE(m.hook,'') hook,"
        "COALESCE(m.recap,'') recap,COALESCE(m.next_brief,'') next_brief,"
        "COALESCE(s.volume_id,'') volume_id,COALESCE(s.plan_id,'') plan_id,"
        "COALESCE(s.scenes_json,'[]') scenes_json "
        "FROM document_node n JOIN document_revision r ON r.id="
        "(SELECT rr.id FROM document_revision rr WHERE rr.document_node_id=n.id "
        "AND rr.branch_id=n.branch_id ORDER BY rr.rowid DESC LIMIT 1) "
        "LEFT JOIN app_chapter_meta m ON m.chapter_id=n.id "
        "LEFT JOIN app_chapter_structure s ON s.chapter_id=n.id "
        "WHERE n.project_id=? AND n.branch_id=? AND n.kind='chapter' "
        "AND NOT EXISTS(SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id) "
        "ORDER BY n.ordinal,n.id", (api.project_id(), api.branch_id()))]


def _current(api, conn, chapter_id):
    chapter_id = api.checked_id(chapter_id, "chapter id")
    catalogue = _catalogue(api, conn)
    chapter = next((item for item in catalogue if item["id"] == chapter_id), None)
    if chapter is None:
        raise TransitionConflict("当前作品中没有这章正文，请重新选择章节。")
    return chapter, catalogue


def capture_source(api, conn, chapter_id):
    """Capture manuscript, canon, review and planning identities for confirmation."""
    chapter, catalogue = _current(api, conn, chapter_id)
    branch = conn.execute(
        "SELECT head_revision,status FROM branch WHERE id=? AND project_id=?",
        (api.branch_id(), api.project_id())).fetchone()
    project = conn.execute(
        "SELECT COALESCE(m.book_status,'writing') book_status,"
        "COALESCE(w.archived,0) archived FROM project p "
        "LEFT JOIN app_project_meta m ON m.project_id=p.id "
        "LEFT JOIN app_workspace w ON w.project_id=p.id WHERE p.id=?",
        (api.project_id(),)).fetchone()
    if branch is None or project is None:
        raise TransitionConflict("作品或创作分支已不存在，请重新打开作品。")
    blueprint = api.workspace.blueprint_dependency(
        conn, api.project_id(), api.branch_id())
    commits = [dict(row) for row in conn.execute(
        "SELECT id,chapter_id,stage,status,revision_id,review_json,snapshot_json,override_reason "
        "FROM app_chapter_commit WHERE project_id=? AND branch_id=? ORDER BY id",
        (api.project_id(), api.branch_id()))]
    return {
        "version": SOURCE_VERSION,
        "project_id": api.project_id(),
        "branch_id": api.branch_id(),
        "chapter_id": chapter["id"],
        "source_revision_id": chapter["revision_id"],
        "source_body_hash": chapter["content_hash"],
        "chapter_metadata_hash": _hash(api, {key: chapter[key] for key in META_FIELDS}),
        "branch_head": branch["head_revision"],
        "catalogue_hash": _hash(api, catalogue),
        "blueprint_version": blueprint["version"],
        "blueprint_hash": blueprint["blueprint_hash"],
        "review_fingerprint": api.review_fingerprint(conn, chapter["id"]),
        "commits_hash": _hash(api, commits),
        "book_status": project["book_status"],
        "project_archived": bool(project["archived"]),
        "branch_status": branch["status"],
    }


@contextmanager
def _atomic(conn):
    # Keep the chat action and this receipt in the caller's one transaction.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    name = "chapter_transition_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + name)
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO " + name)
        conn.execute("RELEASE " + name)
        raise
    else:
        conn.execute("RELEASE " + name)


def _unresolved_memories(api, conn, chapter_id):
    rows = conn.execute(
        "SELECT cm.id,cm.status,cm.payload_json,ss.document_node_id source_chapter_id,"
        "ss.status source_status,ss.revision_id,"
        "(SELECT rr.id FROM document_revision rr WHERE rr.document_node_id=ss.document_node_id "
        "AND rr.branch_id=cm.branch_id ORDER BY rr.rowid DESC LIMIT 1) current_revision_id "
        "FROM candidate_memory cm LEFT JOIN source_span ss ON ss.id=cm.source_span_id "
        "WHERE cm.project_id=? AND cm.branch_id=?",
        (api.project_id(), api.branch_id())).fetchall()
    unresolved = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            raise TransitionBlocked("记忆内容无法读取，请先核对本章记忆。") from None
        if not isinstance(payload, dict):
            raise TransitionBlocked("记忆内容无法读取，请先核对本章记忆。")
        if chapter_id not in {payload.get("source_chapter_id"), row["source_chapter_id"]}:
            continue
        stale_accepted = row["status"] == "accepted" and row["source_chapter_id"] and (
            row["source_status"] != "valid" or row["revision_id"] != row["current_revision_id"])
        if row["status"] not in {"accepted", "rejected", "retracted"} or stale_accepted:
            unresolved.append(row["id"])
    return unresolved


def _require_passed(result):
    if result.get("status") == "passed":
        return
    review = result.get("review") or {}
    issues = review.get("blocking") or []
    message = review.get("message") or next(
        (item.get("message") for item in issues if item.get("message")), "")
    raise TransitionBlocked("本章归档检查未通过" + ("：" + message if message else "，请先处理本章问题。"))


def _has_unmaterialized_successor(api, conn, chapter):
    blueprint = api.workspace.blueprint_row(
        conn, api.project_id(), api.branch_id())["blueprint"]
    plans = [planned for volume in blueprint.get("volumes", [])
             for planned in volume.get("chapters", [])]
    for index, planned in enumerate(plans):
        if ((chapter["plan_id"] and planned.get("id") == chapter["plan_id"])
                or planned.get("chapter_id") == chapter["id"]):
            return index + 1 < len(plans)
    return False


def complete_and_open_next(api, conn, chapter_id, *, expected_source, request_id):
    """Return a durable successor receipt; commit and dispatch remain with the caller."""
    chapter_id = api.checked_id(chapter_id, "chapter id")
    request_id = api.checked_id(request_id, "chapter transition request")
    if not isinstance(expected_source, dict) or set(expected_source) != SOURCE_FIELDS:
        raise TransitionConflict("下一章建议缺少完整来源，请带入本章重新讨论。")
    request_hash = _hash(api, {"chapter_id": chapter_id, "source": expected_source})
    receipt_key = "chapter_transition_" + _hash(
        api, [api.project_id(), api.branch_id(), request_id])
    with _atomic(conn):
        previous = conn.execute(
            "SELECT project_id,branch_id,aggregate_id,event_type,payload_json "
            "FROM event_log WHERE idempotency_key=?", (receipt_key,)).fetchone()
        if previous:
            receipt = json.loads(previous["payload_json"])
            if (previous["project_id"] != api.project_id()
                    or previous["branch_id"] != api.branch_id()
                    or previous["aggregate_id"] != chapter_id
                    or previous["event_type"] != EVENT_TYPE
                    or receipt.get("request_hash") != request_hash):
                raise TransitionConflict("这个下一章请求已保存，请回看原步骤或重新讨论。")
            _current(api, conn, receipt["result"]["chapter_id"])
            return {**receipt["result"], "idempotent": True}
        current_source = capture_source(api, conn, chapter_id)
        if _hash(api, expected_source) != _hash(api, current_source):
            raise TransitionConflict("章节、记忆、归档或目录已经变化，请按当前内容重新讨论下一章。")
        if current_source["project_archived"]:
            raise TransitionBlocked("作品已归档，请先从书架恢复作品，再继续下一章。")
        if current_source["branch_status"] != "active":
            raise TransitionBlocked("当前创作分支不可写，请切换到可写分支。")
        if current_source["book_status"] == "complete":
            raise TransitionBlocked("全书已完稿，请先恢复写作，再继续下一章。")
        chapter, catalogue = _current(api, conn, chapter_id)
        row = conn.execute(
            "SELECT canonical_text FROM document_revision WHERE id=?",
            (chapter["revision_id"],)).fetchone()
        body = bytes(row["canonical_text"]).decode("utf-8", errors="strict")
        if len(re.sub(r"\s", "", body)) < 60:
            raise TransitionBlocked("先写出至少60字的完整正文，再归档并进入下一章。")
        missing = [label for field, label in (
            ("chapter_goal", "本章目标"), ("scene_plan", "场景计划"), ("pov", "视角"))
            if not chapter[field].strip()]
        if missing:
            raise TransitionBlocked("请先补齐" + "、".join(missing) + "，再归档本章。")
        if _unresolved_memories(api, conn, chapter_id):
            raise TransitionBlocked("本章还有待决定或证据过期的记忆，请先确认、拒绝或重新定位。")
        index = next(index for index, item in enumerate(catalogue) if item["id"] == chapter_id)
        successor = catalogue[index + 1] if index + 1 < len(catalogue) else None
        if successor is None and _has_unmaterialized_successor(api, conn, chapter):
            raise TransitionBlocked("蓝图中还有下一章计划，请先将卷章计划接入正文目录，再继续下一章。")
        commits = []
        for stage in ("prewrite", "precommit", "postcommit"):
            if stage == "precommit" and chapter["status"] != "完成":
                if not api.update_chapter(conn, chapter_id, {"status": "完成"}):
                    raise TransitionConflict("本章已经变化，请重新选择章节。")
            result = api.create_chapter_commit(conn, chapter_id, stage)
            _require_passed(result)
            commits.append({key: result[key] for key in ("id", "stage", "status", "revision_id")})
        created = successor is None
        if successor is None:
            next_id = api.create_chapter(conn, {
                "title": f"第 {len(catalogue) + 1} 章", "body": "", "status": "草稿",
                "pov": chapter["pov"],
            })
        else:
            next_id = successor["id"]
        result = {
            "chapter_id": next_id,
            "next_chapter_id": next_id,
            "previous_chapter_id": chapter_id,
            "created": created,
            "idempotent": False,
            "commits": commits,
        }
        api.append_event(
            conn, chapter_id, EVENT_TYPE,
            {"request_hash": request_hash, "result": result},
            idempotency_key=receipt_key)
        return result
