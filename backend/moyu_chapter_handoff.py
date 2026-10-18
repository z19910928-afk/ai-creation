"""Author-confirmed chapter preparation from the immediately preceding handoff."""
from __future__ import annotations

import json
import re


FIELDS = ("chapter_goal", "scene_plan", "pov")


class HandoffConflict(ValueError):
    status = 409


class HandoffNotFound(ValueError):
    status = 404


def _source(api, conn, chapter_id):
    chapter_id = api.checked_id(chapter_id, "chapter id")
    workspace = conn.execute(
        "SELECT b.status,COALESCE(w.archived,0) archived,"
        "COALESCE(m.book_status,'writing') book_status FROM branch b "
        "LEFT JOIN app_workspace w ON w.project_id=b.project_id "
        "LEFT JOIN app_project_meta m ON m.project_id=b.project_id "
        "WHERE b.id=? AND b.project_id=?", (api.branch_id(), api.project_id()),
    ).fetchone()
    if workspace is None:
        raise HandoffNotFound("当前作品版本已不存在，请重新打开作品。")
    if workspace["archived"]:
        raise ValueError("作品已归档，请先从书架恢复作品再准备本章。")
    if workspace["status"] != "active":
        raise ValueError("当前作品版本已封存，请切换到正在写作的版本。")
    if workspace["book_status"] == "complete":
        raise ValueError("全书已完稿，请先恢复写作再准备本章。")
    chapters = api.chapter_rows(conn)
    index = next((i for i, chapter in enumerate(chapters)
                  if chapter["id"] == chapter_id), None)
    if index is None:
        raise HandoffNotFound("当前作品中没有这一章，请重新打开章节。")
    if index == 0:
        raise ValueError("这是第一章，还没有上章交接。")
    current, previous = chapters[index], chapters[index - 1]
    if not previous["next_brief"].strip():
        raise ValueError("上章还没有交接内容，请先填写上章的下一章任务。")
    if current["status"] == "完成":
        raise ValueError("本章已经完成，请先将章节状态改为草稿或进行中。")
    keys = ("id", "title", "revision_id", "body_hash", "status", "plan_id",
            "volume_id", "scenes", *api.CHAPTER_META_TEXT_LIMITS)
    frozen = {"project_id": api.project_id(), "branch_id": api.branch_id(),
              "workspace": dict(workspace),
              "current": {key: current.get(key) for key in keys},
              "previous": {key: previous.get(key) for key in keys}}
    token = api.hash_text(json.dumps(frozen, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")))
    return current, previous, token


def preview(api, conn, chapter_id):
    current, previous, token = _source(api, conn, chapter_id)
    draft = {key: current[key] for key in FIELDS}
    suggested, warnings = [], []
    if not draft["chapter_goal"].strip():
        if len(previous["next_brief"]) <= api.CHAPTER_META_TEXT_LIMITS["chapter_goal"]:
            draft["chapter_goal"] = previous["next_brief"]
            suggested.append("chapter_goal")
        else:
            warnings.append("上章交接较长，请整理为不超过5000字的本章目标。")
    if not draft["pov"].strip() and previous["pov"].strip():
        draft["pov"] = previous["pov"]
        suggested.append("pov")
    return {"chapter_id": current["id"], "source_token": token,
            "previous": {key: previous[key]
                         for key in ("id", "title", "recap", "next_brief", "pov")},
            "draft": draft, "suggested_fields": suggested, "warnings": warnings}


def apply(api, conn, chapter_id, data):
    if not isinstance(data, dict) or set(data) - {"request_id", "source_token", *FIELDS}:
        raise ValueError("本章准备内容包含不支持的选项。")
    chapter_id = api.checked_id(chapter_id, "chapter id")
    request_id = api.checked_id(data.get("request_id", ""), "handoff request")
    token = data.get("source_token")
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ValueError("请重新打开上章交接，再确认本章任务。")
    values = {}
    for key in FIELDS:
        value = data.get(key)
        if not isinstance(value, str):
            raise ValueError("本章目标、场景计划和视角需要填写文字。")
        values[key] = api.bounded_text(value, api.CHAPTER_META_TEXT_LIMITS[key], key)
    if not values["chapter_goal"].strip():
        raise ValueError("请填写本章目标，再确认承接。")
    fingerprint = api.hash_text(json.dumps(
        {"chapter_id": chapter_id, "source_token": token, **values},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    event_key = "handoff_" + api.hash_text(api.json_text(
        [api.project_id(), api.branch_id(), chapter_id, request_id]))
    previous_request = conn.execute(
        "SELECT payload_json FROM event_log WHERE project_id=? AND branch_id=? "
        "AND aggregate_id=? AND event_type='chapter.handoff_prepared' AND idempotency_key=?",
        (api.project_id(), api.branch_id(), chapter_id, event_key),
    ).fetchone()
    if previous_request:
        if json.loads(previous_request["payload_json"]).get("request_hash") != fingerprint:
            raise HandoffConflict("这次承接已经保存，请重新打开本章任务后再修改。")
        return {"chapter_id": chapter_id, "idempotent": True,
                "state": api.state_from_db(conn)}
    current, previous, expected = _source(api, conn, chapter_id)
    if token != expected:
        raise HandoffConflict("本章或上章交接已经变化，请重新打开承接内容后再确认。")
    if not api.update_chapter(conn, current["id"], values):
        raise HandoffNotFound("当前作品中没有这一章，请重新打开章节。")
    api.append_event(conn, current["id"], "chapter.handoff_prepared",
                     {"request_hash": fingerprint, "source_token": token,
                      "previous_chapter_id": previous["id"], "fields": values},
                     idempotency_key=event_key)
    return {"chapter_id": chapter_id, "idempotent": False,
            "state": api.state_from_db(conn)}
