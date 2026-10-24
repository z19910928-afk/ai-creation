"""Scoped author conversations and proposals bound to durable writing workflows."""
from __future__ import annotations

import json

from . import moyu_agent_service as writing_agent
from . import moyu_agent_settings as connection
from . import moyu_chapter_transition as chapter_transition
from . import moyu_context
from . import moyu_materials
from . import moyu_prompt_templates
from . import moyu_records
from . import moyu_workspace
from .moyu_provider import (
    ProviderCanceled, ProviderError, normalize_chat_reply, prepare_chat_request, validate_json,
)


class ChatNotFound(LookupError):
    """A conversation resource is unavailable in the requested workspace."""


KINDS = {"continue", "polish", "inspect", "plan", "next_chapter", "open", "save_inspiration"}
VIEWS = {"blueprint", "memory", "story", "editor", "review", "world"}
_ACTION_FIELDS = {
    "kind": {"type": "string", "enum": sorted(KINDS - {"save_inspiration"})},
    "label": {"type": "string", "minLength": 1, "maxLength": 80},
    "instruction": {"type": "string", "maxLength": 2000},
    "view": {"type": "string", "enum": sorted(VIEWS)},
    "reference_index": {"type": "integer", "minimum": 0, "maximum": 2},
}
ACTION_SCHEMA = {"oneOf": [
    {"type": "object", "required": ["kind", "label", "instruction"],
     "additionalProperties": False, "properties": _ACTION_FIELDS},
    {"type": "object", "required": ["kind", "label", "instruction", "title", "content"],
     "additionalProperties": False, "properties": {
         "kind": {"type": "string", "enum": ["save_inspiration"]},
         "label": _ACTION_FIELDS["label"], "instruction": _ACTION_FIELDS["instruction"],
         "title": {"type": "string", "minLength": 1, "maxLength": 80},
         "content": {"type": "string", "minLength": 1, "maxLength": 6000},
     }},
]}
SCHEMA = {
    "type": "object", "required": ["reply", "actions"], "additionalProperties": False,
    "properties": {
        "reply": {"type": "string", "minLength": 1, "maxLength": 16000},
        "actions": {"type": "array", "maxItems": 4, "items": ACTION_SCHEMA},
    },
}
REPLY_SCHEMA = {
    **SCHEMA, "properties": {
        "reply": SCHEMA["properties"]["reply"],
        "actions": {"type": "array", "maxItems": 64},
    },
}
STALE_REPLY_NOTE = "回复参考的是发送时的资料；资料已更新，本轮操作建议已取消。"
SYSTEM = moyu_prompt_templates.render(
    "chat.system", schema=json.dumps(SCHEMA, ensure_ascii=False, separators=(",", ":")))


def install_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS app_agent_chat_session(
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(id),
            branch_id TEXT NOT NULL REFERENCES branch(id),
            title TEXT NOT NULL,
            request_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            restored_read_only INTEGER NOT NULL DEFAULT 0,
            UNIQUE(project_id,branch_id,request_id));
        CREATE TABLE IF NOT EXISTS app_agent_chat_turn(
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(id),
            branch_id TEXT NOT NULL REFERENCES branch(id),
            session_id TEXT NOT NULL REFERENCES app_agent_chat_session(id),
            request_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            content TEXT NOT NULL,
            references_json TEXT NOT NULL,
            chapter_id TEXT REFERENCES document_node(id),
            source_json TEXT NOT NULL,
            context_json TEXT NOT NULL,
            job_id TEXT NOT NULL UNIQUE REFERENCES app_ai_job(id),
            reply TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(project_id,branch_id,session_id,request_id));
        CREATE TABLE IF NOT EXISTS app_agent_chat_action(
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES project(id),
            branch_id TEXT NOT NULL REFERENCES branch(id),
            session_id TEXT NOT NULL REFERENCES app_agent_chat_session(id),
            turn_id TEXT NOT NULL REFERENCES app_agent_chat_turn(id),
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            instruction TEXT NOT NULL,
            chapter_id TEXT REFERENCES document_node(id),
            source_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS app_chat_turn_session
            ON app_agent_chat_turn(project_id,branch_id,session_id);
        CREATE INDEX IF NOT EXISTS app_chat_action_turn
            ON app_agent_chat_action(project_id,branch_id,turn_id);
    """)


def _json(value, fallback):
    return json.loads(value) if value else fallback


def _scope(api):
    return api.project_id(), api.branch_id()


def _session(api, conn, session_id, *, writable=False):
    session_id = api.checked_id(session_id, "chat session")
    row = conn.execute(
        "SELECT * FROM app_agent_chat_session WHERE id=? AND project_id=? AND branch_id=?",
        (session_id, *_scope(api)),
    ).fetchone()
    if row is None:
        raise ChatNotFound("当前作品中没有这条对话。")
    if writable and row["restored_read_only"]:
        raise ValueError("备份恢复的对话仅供回看，请新建对话继续。")
    return row


def _public_session(row):
    return {key: bool(row[key]) if key == "restored_read_only" else row[key]
            for key in ("id", "project_id", "branch_id", "title", "created_at",
                        "updated_at", "restored_read_only")}


def _public_action(row):
    result = {**{key: row[key] for key in (
        "id", "kind", "label", "instruction", "chapter_id", "status", "created_at")},
        "result": _json(row["result_json"], {})}
    if row["kind"] == "save_inspiration":
        payload = _json(row["payload_json"], {})
        result.update(title=payload.get("title", ""), content=payload.get("content", ""))
    return result


def _context_summary(turn):
    context = _json(turn["context_json"], {}) if turn else {}
    chapter = context.get("current_chapter") or {}
    material_ids = {
        item["id"] for collection in ("story_cards", "author_plans", "confirmed_memory")
        for item in context.get(collection, []) if isinstance(item, dict) and item.get("id")
    }
    return {
        "project_materials_included": bool(context.get("project_materials_included")),
        "chapter_included": bool(context.get("chapter_context_included")),
        "chapter_title": str(chapter.get("title") or ""),
        "reference_count": len(context.get("quoted_manuscript") or []),
        "material_count": len(material_ids),
        "history_truncated": bool((context.get("conversation_history") or {}).get("truncated")),
    }


def _recovered_reply(turn):
    if turn["reply"] or turn["job_status"] != "failed":
        return ""
    result = _json(turn["job_result_json"], {})
    if (result.get("error_code") != "invalid_structured_output"
            or result.get("finish_reason") != "stop"
            or not (result.get("provider_sse_complete") is True
                    or result.get("stream_transport") == "provider_json")):
        return ""
    try:
        return normalize_chat_reply(result.get("text") or "", SCHEMA)["reply"]
    except ProviderError:
        return ""


def list_sessions(api, conn):
    rows = conn.execute(
        "SELECT * FROM app_agent_chat_session WHERE project_id=? AND branch_id=? "
        "ORDER BY updated_at DESC,rowid DESC LIMIT 100", _scope(api)).fetchall()
    return {"sessions": [_public_session(row) for row in rows],
            "settings": connection.public_settings(conn, api.project_id())}


def get_session(api, conn, session_id):
    session = _session(api, conn, session_id)
    turns = list(reversed(conn.execute(
        "SELECT t.*,j.status AS job_status,j.error AS job_error,j.result_json AS job_result_json "
        "FROM app_agent_chat_turn t JOIN app_ai_job j ON j.id=t.job_id "
        "AND j.project_id=t.project_id AND j.branch_id=t.branch_id "
        "WHERE t.session_id=? AND t.project_id=? AND t.branch_id=? "
        "ORDER BY t.rowid DESC LIMIT 100", (session_id, *_scope(api))).fetchall()))
    messages = []
    for turn in turns:
        actions = conn.execute(
            "SELECT * FROM app_agent_chat_action WHERE turn_id=? AND project_id=? "
            "AND branch_id=? ORDER BY rowid", (turn["id"], *_scope(api))).fetchall()
        messages.append({"id": turn["id"] + "_user", "role": "user",
                         "content": turn["content"], "created_at": turn["created_at"],
                         "references": _json(turn["references_json"], [])})
        status = turn["job_status"]
        recovered = _recovered_reply(turn)
        stale = _json(turn["job_result_json"], {}).get("chat_source_stale") is True
        if turn["reply"] or status in {"failed", "canceled"}:
            messages.append({"id": turn["id"] + "_assistant", "role": "assistant",
                             "content": turn["reply"] or recovered, "created_at": turn["updated_at"],
                             "status": status,
                             "actions": [] if recovered or stale else [_public_action(row) for row in actions],
                             **({"context_stale": True} if stale else {}),
                             **({"response_recovered": True} if recovered else {})})
    latest = turns[-1] if turns else None
    run = None
    if latest:
        run = {"id": latest["job_id"], "status": latest["job_status"],
               "error": latest["job_error"]}
    return {"session": _public_session(session), "messages": messages, "active_run": run,
            "context_summary": _context_summary(latest),
            "settings": connection.public_settings(conn, api.project_id())}


def create_session(api, conn, data):
    if set(data) - {"request_id"}:
        raise ValueError("新对话包含不支持的选项。")
    request_id = api.checked_id(data.get("request_id", ""), "chat request")
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    previous = conn.execute(
        "SELECT * FROM app_agent_chat_session WHERE project_id=? AND branch_id=? "
        "AND request_id=?", (*_scope(api), request_id)).fetchone()
    if previous:
        return {"session": _public_session(previous), "idempotent": True}
    session_id, created = api.uid("chat_"), api.now()
    conn.execute("INSERT INTO app_agent_chat_session VALUES(?,?,?,?,?,?,?,0)",
                 (session_id, *_scope(api), "新对话", request_id, created, created))
    return {"session": _public_session(_session(api, conn, session_id)), "idempotent": False}


def _chapter(api, conn, chapter_id):
    chapter = next((c for c in api.chapter_rows(conn) if c["id"] == chapter_id), None)
    if chapter is None:
        raise ValueError("引用的章节已不存在，请重新选择当前作品中的正文。")
    return chapter


def _assert_source(api, conn, source):
    if not isinstance(source, dict) or not source:
        raise ValueError("建议没有当前原稿依据，请带入章节后重新讨论。")
    chapter = _chapter(api, conn, source.get("chapter_id"))
    identity = {"project_id": api.project_id(), "branch_id": api.branch_id(),
                "chapter_id": chapter["id"], "source_revision_id": chapter["revision_id"],
                "source_body_hash": api.hash_text(chapter["body"])}
    if any(source.get(key) != value for key, value in identity.items()):
        raise ValueError("原稿已变化，请按当前正文重新讨论；历史建议不会覆盖新稿。")
    return chapter


def _references(api, conn, values):
    if not isinstance(values, list) or len(values) > 3:
        raise ValueError("一次对话最多引用三处正文。")
    result = []
    for source in values:
        chapter = _assert_source(api, conn, source)
        if set(source) - {"project_id", "branch_id", "chapter_id", "source_revision_id",
                          "source_body_hash", "range", "mode"}:
            raise ValueError("正文引用包含不支持的选项。")
        canonical = api.candidate_source(conn, "polish", chapter, source)
        if len(canonical["range"]["quote"]) > 12000:
            raise ValueError("单处引用最多12000字，请缩小选区。")
        result.append(canonical)
    return result


def _excerpt(text, limit):
    text = str(text or "")
    marker = "\n[内容已缩短]"
    return text if len(text) <= limit else text[:max(0, limit - len(marker))] + marker


def _project_context(api, conn, instruction):
    """Discuss the author's materials without selecting or sending any prose."""
    state = api.state_from_db(conn)
    terms = set(moyu_context.lexical_terms(instruction))

    def relevance(item):
        text = " ".join(str(item.get(key) or "") for key in
                        ("name", "description", "title", "text"))
        return -len(terms.intersection(moyu_context.lexical_terms(text)))

    records = [
        {**item, "name": _excerpt(item.get("name"), 160),
         "description": _excerpt(item.get("description"), 1000)}
        for item in state.get("records") or []
        if moyu_context.scope_decision(
            item, moyu_context.ScopePolicy(*_scope(api), narrator_mode="omniscient",
                                          allow_plans=True), kind="setting")["allowed"]
    ]
    records.sort(key=relevance)
    records = records[:24]
    safe_state = {
        "project": {**state["project"],
                    "summary": _excerpt(state["project"].get("summary"), 1200)},
        "records": records, "plot": {}, "chapters": [], "memory": [],
    }
    blueprint = moyu_workspace.blueprint_row(conn, *_scope(api))
    limits = {"premise": 400, "genre": 100, "audience": 200, "tone": 300,
              "contract": 600, "outline": 800}
    planned = {key: _excerpt(blueprint["blueprint"].get(key), limit)
               for key, limit in limits.items()}
    guidance = moyu_context.author_guidance(
        safe_state, None, {"blueprint": planned, "version": blueprint["version"]},
        project_id=api.project_id(), branch_id=api.branch_id()) or {
            "epistemic_mode": "author_plan",
            "use": "writing_guidance_not_occurred_not_character_knowledge",
        }
    guidance["outline"] = planned["outline"]
    volumes = blueprint["blueprint"].get("volumes") or []
    guidance["volumes"] = [
        {"id": item["id"], "title": _excerpt(item.get("title"), 80),
         "summary": _excerpt(item.get("summary"), 200)}
        for item in volumes[:3]
    ]
    guidance["volumes_omitted"] = max(0, len(volumes) - len(guidance["volumes"]))
    context, _ = moyu_context.assemble_context(
        safe_state, None, instruction=instruction, task="brainstorm", guidance=guidance,
        project_id=api.project_id(), branch_id=api.branch_id(),
        profile={"max_tokens": 24000 + len(instruction.encode("utf-8")),
                 "chapter_top_k": 0, "recent_window": 0,
                 "memory_top_k": 0, "record_top_k": 12})
    plot_items = [
        {**item, "stage": stage, "text": _excerpt(item.get("text"), 400),
         "epistemic_mode": "author_plan", "use": "planning_only_not_occurred"}
        for stage, items in (state.get("plot") or {}).items() for item in items
    ]
    context["author_plans"].extend(sorted(plot_items, key=relevance)[:6])
    context["project_material_selection"] = {
        "mode": "overview_without_manuscript",
        "records_available": len(state.get("records") or []),
        "records_considered": len(records),
        "initial_budget_items_omitted": len(
            context["context_budget"]["truncation"]["removed"]),
    }
    context = moyu_context.fit_budget(
        context, 24000 + len(instruction.encode("utf-8")),
        reserve_bytes=1800 + len(instruction.encode("utf-8")))
    context["chapter_context_included"] = False
    context["project_materials_included"] = True
    context["_project_guidance_source"] = moyu_workspace.blueprint_dependency(conn, *_scope(api))
    context["_material_source"] = moyu_materials.capture(
        conn, *_scope(api), context, state=state)
    return context


def _validate_project_guidance(api, conn, context):
    source = context.get("_project_guidance_source")
    if source is not None and source != moyu_workspace.blueprint_dependency(conn, *_scope(api)):
        raise ValueError("作品规划已变化，请按当前规划重新讨论。")


def _history_pair(api, row, actions, limit):
    references = _json(row["references_json"], [])
    compact_references = [
        {"chapter_id": ref.get("chapter_id"),
         "source_revision_id": ref.get("source_revision_id"),
         "quote_excerpt": _excerpt((ref.get("range") or {}).get("quote"), 300),
         "quote_characters": len((ref.get("range") or {}).get("quote") or ""),
         "quote_truncated": len((ref.get("range") or {}).get("quote") or "") > 300}
        for ref in references[:3]
    ]
    compact_actions = []
    for action in actions[:4]:
        public = _public_action(action)
        compact_actions.append({
            "kind": public["kind"], "label": public["label"], "status": public["status"],
            "instruction": _excerpt(public["instruction"], 200),
            **({"title": public["title"]} if public["kind"] == "save_inspiration" else {}),
            "result": {key: value for key, value in public["result"].items()
                       if key in {"view", "record_id", "writing_session_id", "job_id", "chapter_id"}},
        })
    user = row["content"]
    if compact_references:
        user += "\n<historical_quotes>" + api.json_text(compact_references) + "</historical_quotes>"
    stale = _json(row["job_result_json"], {}).get("chat_source_stale") is True
    assistant = {"reply": row["reply"], "actions": [] if stale else compact_actions,
                 **({"context_stale": True, "context_note": STALE_REPLY_NOTE} if stale else {})}
    truncated = bool(references or any(
        len(action["instruction"]) > 200 or action["kind"] == "save_inspiration"
        for action in actions))
    if len(user) + len(api.json_text(assistant)) > limit:
        # Keep the recent question and answer even when reference metadata or
        # one unusually long reply would otherwise evict the whole history.
        user = _excerpt(row["content"], min(4000, limit // 3))
        if references:
            user += f"\n[历史引用已省略，共{len(references)}处；需要原句时请重新引用]"
        assistant["actions"] = [] if stale else [
            {key: action[key] for key in ("kind", "label", "status")}
            for action in compact_actions
        ]
        assistant["history_truncated"] = True
        assistant["reply"] = _excerpt(
            row["reply"], max(100, limit - len(user) - len(api.json_text({
                **assistant, "reply": ""})) - 40))
        while len(user) + len(api.json_text(assistant)) > limit:
            overflow = len(user) + len(api.json_text(assistant)) - limit
            assistant["reply"] = _excerpt(
                assistant["reply"], max(20, len(assistant["reply"]) - overflow - 10))
        truncated = True
    return [{"role": "user", "content": user},
            {"role": "assistant", "content": api.json_text(assistant)}], truncated


def _messages(api, conn, session_id, context, content):
    rows = conn.execute(
        "SELECT t.id,t.content,t.reply,t.references_json,j.result_json AS job_result_json "
        "FROM app_agent_chat_turn t JOIN app_ai_job j ON j.id=t.job_id "
        "AND j.project_id=t.project_id AND j.branch_id=t.branch_id "
        "WHERE t.session_id=? AND t.project_id=? AND t.branch_id=? AND t.status='success' "
        "ORDER BY t.rowid DESC LIMIT 13", (session_id, *_scope(api))).fetchall()
    history, size, truncated = [], 0, len(rows) > 12
    for row in rows[:12]:
        if 24000 - size < 2000:
            truncated = True
            break
        actions = conn.execute(
            "SELECT * FROM app_agent_chat_action WHERE turn_id=? AND project_id=? AND branch_id=?",
            (row["id"], *_scope(api))).fetchall()
        pair, shortened = _history_pair(api, row, actions, min(12000, 24000 - size))
        history[0:0] = pair
        size += sum(len(message["content"]) for message in pair)
        truncated = truncated or shortened
    context["conversation_history"] = {
        "turns_included": len(history) // 2, "truncated": truncated,
        "character_budget": 24000, "characters_used": size,
        "policy": "历史摘录只用于讨论，不能作为本次修改正文的来源。",
    }
    material = {key: value for key, value in context.items() if not key.startswith("_")}
    return [{"role": "system",
             "content": moyu_prompt_templates.render(
                 "chat.system", schema=json.dumps(
                     SCHEMA, ensure_ascii=False, separators=(",", ":")))},
            *history,
            {"role": "user", "content": moyu_prompt_templates.render(
                "chat.user", material=json.dumps(
                    material, ensure_ascii=False, separators=(",", ":")), content=content)}]


def post_message(api, conn, session_id, data):
    if set(data) - {"request_id", "content", "chapter_id", "include_chapter", "references"}:
        raise ValueError("对话包含不支持的选项。")
    request_id = api.checked_id(data.get("request_id", ""), "chat message request")
    content = data.get("content")
    if not isinstance(content, str) or not content.strip() or len(content) > 6000:
        raise ValueError("请输入1至6000字的消息。")
    content = content.strip()
    include = data.get("include_chapter", True)
    if not isinstance(include, bool):
        raise ValueError("是否带入当前章节的选项无效。")
    chapter_id = data.get("chapter_id")
    if chapter_id is not None:
        chapter_id = api.checked_id(chapter_id, "chapter id")
    fingerprint = api.hash_text(json.dumps(
        {"content": content, "chapter_id": chapter_id, "include_chapter": include,
         "references": data.get("references", [])}, ensure_ascii=False, sort_keys=True))
    _session(api, conn, session_id, writable=True)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    session = _session(api, conn, session_id, writable=True)
    previous = conn.execute(
        "SELECT request_hash FROM app_agent_chat_turn WHERE session_id=? AND project_id=? "
        "AND branch_id=? AND request_id=?", (session_id, *_scope(api), request_id)).fetchone()
    if previous:
        if previous["request_hash"] != fingerprint:
            raise ValueError("同一次消息的内容已变化，请创建新的消息。")
        return {**get_session(api, conn, session_id), "idempotent": True}
    active = conn.execute(
        "SELECT 1 FROM app_agent_chat_turn t JOIN app_ai_job j ON j.id=t.job_id "
        "WHERE t.session_id=? AND t.project_id=? AND t.branch_id=? "
        "AND j.status IN ('queued','running')", (session_id, *_scope(api))).fetchone()
    if active:
        raise ValueError("总管正在处理上一条消息，请等待回复或先停止。")
    config = connection.provider_settings(conn, api.project_id())
    if not config.get("base_url") or not config.get("model"):
        raise ValueError("请先在设置中配置写作总管的连接，再开始对话。")
    references = _references(api, conn, data.get("references", []))
    source = None
    if include and chapter_id:
        context, chapter = api.ai_context(conn, chapter_id, instruction=content, task="summary")
        context["project_materials_included"] = True
        source = api.candidate_source(conn, "summary", chapter)
        context["_chapter_transition_source"] = chapter_transition.capture_source(
            api, conn, chapter_id)
    else:
        context = _project_context(api, conn, content)
        if references:
            source = references[0]
            chapter_id = source["chapter_id"]
            context["current_chapter"] = {"id": chapter_id, "full_content": False}
    context["chapter_context_included"] = bool(include and chapter_id)
    context["quoted_manuscript"] = references
    context["available_actions"] = sorted(KINDS)
    if "_material_source" not in context:
        context["_material_source"] = moyu_materials.capture(
            conn, api.project_id(), api.branch_id(), context)
    dependencies = {
        (item["kind"], item["id"]): item
        for item in context["_material_source"]["dependencies"]
    }
    for reference in references:
        marker = moyu_materials.capture(
            conn, api.project_id(), api.branch_id(),
            {"current_chapter": {"id": reference["chapter_id"]}})
        dependencies.update({(item["kind"], item["id"]): item
                             for item in marker["dependencies"]})
    context["_material_source"]["dependencies"] = [
        dependencies[key] for key in sorted(dependencies)
    ]
    progress = conn.execute(
        "SELECT * FROM app_agent_session WHERE project_id=? AND branch_id=? "
        "AND restored_read_only=0 ORDER BY rowid DESC LIMIT 5", _scope(api)).fetchall()
    context["writing_progress"] = [{
        key: value for key, value in writing_agent.public_session(api, conn, row).items()
        if key in {"id", "chapter_id", "mode", "status", "stage", "calls_used",
                   "max_calls", "steps", "source_stale", "error"}
    } for row in progress]
    turn_id = api.uid("chatturn_")
    context["_agent_chat"] = {"chat_session_id": session_id, "chat_turn_id": turn_id}
    context["_source"] = source
    messages = _messages(api, conn, session_id, context, content)
    max_tokens = api.ai_output_limit(config=config, task="assistant_chat")
    prepared = prepare_chat_request(config, messages, task="assistant_chat",
                                    response_format="json", max_tokens=max_tokens, stream=True)
    queued = api.create_ai_job(conn, "assistant_chat", context, content, "json", max_tokens,
                               provider_config=config, frozen_request=prepared["payload"])
    frozen_context = conn.execute(
        "SELECT context_json FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
        (queued["id"], *_scope(api)),
    ).fetchone()["context_json"]
    created = api.now()
    conn.execute(
        "INSERT INTO app_agent_chat_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (turn_id, *_scope(api), session_id, request_id, fingerprint, content,
         api.json_text(references), chapter_id if source else None, api.json_text(source),
         frozen_context, queued["id"], "", "queued", created, created))
    conn.execute("UPDATE app_agent_chat_session SET title=?,updated_at=? WHERE id=?",
                 (content[:36] if session["title"] == "新对话" else session["title"], created, session_id))
    return {**get_session(api, conn, session_id), "idempotent": False,
            "_start_job": queued["id"]}


def _bound_turn(api, conn, context, *, job_id=None):
    marker = context.get("_agent_chat")
    if not isinstance(marker, dict):
        raise ValueError("对话任务缺少来源，请重新发送消息。")
    _session(api, conn, marker.get("chat_session_id"), writable=True)
    turn = conn.execute(
        "SELECT * FROM app_agent_chat_turn WHERE id=? AND session_id=? "
        "AND project_id=? AND branch_id=?",
        (marker.get("chat_turn_id"), marker.get("chat_session_id"), *_scope(api)),
    ).fetchone()
    if (turn is None or _json(turn["context_json"], {}) != context
            or (job_id is not None and turn["job_id"] != job_id)
            or _json(turn["source_json"], None) != context.get("_source")
            or _json(turn["references_json"], []) != context.get("quoted_manuscript", [])
            or turn["chapter_id"] != (context.get("_source") or {}).get("chapter_id")):
        raise ValueError("对话消息与保存的任务不一致，请重新发送消息。")
    return turn


def validate_dispatch(api, conn, context, *, job_id=None):
    _bound_turn(api, conn, context, job_id=job_id)
    if context.get("_source"):
        _assert_source(api, conn, context["_source"])
    _references(api, conn, context.get("quoted_manuscript", []))
    _validate_project_guidance(api, conn, context)


def finish_turn(api, conn, job, result, status):
    turn = conn.execute(
        "SELECT * FROM app_agent_chat_turn WHERE job_id=? AND project_id=? AND branch_id=?",
        (job["id"], *_scope(api))).fetchone()
    if turn is None:
        if status == "success":
            raise ValueError("对话消息来源缺失。")
        return
    reply = ""
    if status == "success":
        if result.get("completion_state") != "complete" or result.get("truncated"):
            raise ValueError("总管回复尚未完整完成，不能作为已完成对话保存。")
        structured = result.get("structured")
        validate_json(structured, REPLY_SCHEMA)
        if not structured["reply"].strip():
            raise ValueError("总管未返回有效回复，请检查模型连接。")
        context = _json(job["context_json"], {})
        _bound_turn(api, conn, context, job_id=job["id"])
        if job["chapter_id"] != turn["chapter_id"]:
            raise ValueError("对话消息与保存的章节不一致，请重新发送消息。")
        reply = structured["reply"]
        source = _json(turn["source_json"], None)
        references = _json(turn["references_json"], [])
        result.pop("chat_source_stale", None)
        try:
            if source:
                _assert_source(api, conn, source)
            _references(api, conn, references)
            api.validate_material_source(conn, context)
            api.validate_guidance_source(conn, context)
            _validate_project_guidance(api, conn, context)
        except ValueError:
            # The frozen request is intact; changed live materials only disable actions.
            result["chat_source_stale"] = True
            result["safe_to_apply"] = False
            structured = {**structured, "actions": []}
            result["structured"] = structured
        proposed_count = 0
        for proposal in structured["actions"]:
            try:
                validate_json(proposal, ACTION_SCHEMA)
            except ProviderError:
                continue
            if proposed_count == 4:
                break
            kind = proposal["kind"]
            if not proposal["label"].strip():
                continue
            if kind not in {"open", "save_inspiration"} and not source:
                continue
            if kind not in {"open", "save_inspiration"} and not proposal["instruction"].strip():
                continue
            if kind == "open" and proposal.get("view") not in VIEWS:
                continue
            selected = source
            index = proposal.get("reference_index")
            reference_only = not context.get("chapter_context_included")
            if reference_only and kind not in {"open", "polish", "save_inspiration"}:
                continue
            if reference_only and kind == "polish" and index is None:
                continue
            if kind == "polish" and index is not None:
                if index >= len(references):
                    continue
                selected = references[index]
                if selected["chapter_id"] != turn["chapter_id"]:
                    continue
            payload = {"mode": kind}
            if kind == "open":
                payload = {"view": proposal["view"]}
            elif kind == "save_inspiration":
                title, content = proposal["title"].strip(), proposal["content"].strip()
                if not title or not content:
                    continue
                payload = {"title": title, "content": content}
            elif kind == "next_chapter":
                transition_source = context.get("_chapter_transition_source")
                if not isinstance(transition_source, dict) or not transition_source:
                    continue
                payload = {"source": transition_source}
            elif kind == "polish" and index is not None:
                payload["selection"] = True
            created = api.now()
            conn.execute(
                "INSERT INTO app_agent_chat_action VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (api.uid("chatact_"), *_scope(api), turn["session_id"], turn["id"], kind,
                 ("完成本章，打开下一章" if kind == "next_chapter" else proposal["label"]),
                 proposal["instruction"], turn["chapter_id"],
                 api.json_text(selected), api.json_text(payload), "proposed", "{}", created, created))
            proposed_count += 1
    conn.execute("UPDATE app_agent_chat_turn SET reply=?,status=?,updated_at=? WHERE id=?",
                 (reply, status, api.now(), turn["id"]))
    conn.execute("UPDATE app_agent_chat_session SET updated_at=? WHERE id=?",
                 (api.now(), turn["session_id"]))


def get_run(api, conn, run_id):
    row = conn.execute(
        "SELECT session_id FROM app_agent_chat_turn WHERE job_id=? AND project_id=? AND branch_id=?",
        (api.checked_id(run_id, "chat run"), *_scope(api))).fetchone()
    if row is None:
        raise ChatNotFound("当前作品中没有这次对话任务。")
    return get_session(api, conn, row["session_id"])


def cancel_run(api, conn, run_id):
    previous = get_run(api, conn, run_id)
    _session(api, conn, previous["session"]["id"], writable=True)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT j.*,t.session_id FROM app_ai_job j JOIN app_agent_chat_turn t ON t.job_id=j.id "
        "AND t.project_id=j.project_id AND t.branch_id=j.branch_id "
        "WHERE j.id=? AND j.project_id=? AND j.branch_id=?",
        (api.checked_id(run_id, "chat run"), *_scope(api))).fetchone()
    if row is None:
        raise ChatNotFound("当前作品中没有这次对话任务。")
    _session(api, conn, row["session_id"], writable=True)
    if row["status"] == "queued":
        conn.execute("UPDATE app_ai_job SET cancel_requested=1 WHERE id=?", (run_id,))
        api.finish_ai_job(conn, row, {"billing_state": "not_dispatched"},
                          ProviderCanceled(), 0)
    elif row["status"] == "running":
        conn.execute("UPDATE app_ai_job SET cancel_requested=1,updated_at=? WHERE id=?",
                     (api.now(), run_id))
        conn.commit()
        api.cancel_ai_job(run_id)
    return get_session(api, conn, row["session_id"])


def _action(api, conn, action_id):
    row = conn.execute(
        "SELECT * FROM app_agent_chat_action WHERE id=? AND project_id=? AND branch_id=?",
        (api.checked_id(action_id, "chat action"), *_scope(api))).fetchone()
    if row is None:
        raise ChatNotFound("当前作品中没有这条创作建议。")
    _session(api, conn, row["session_id"], writable=True)
    return row


def dismiss_action(api, conn, action_id):
    _action(api, conn, action_id)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    row = _action(api, conn, action_id)
    if row["status"] not in {"proposed", "dismissed"}:
        raise ValueError("这个步骤已经确认，请到任务进度中查看或停止。")
    conn.execute("UPDATE app_agent_chat_action SET status='dismissed',updated_at=? WHERE id=?",
                 (api.now(), action_id))
    return {"action": _public_action(_action(api, conn, action_id))}


def _recover_writing_session(api, conn, action, payload):
    if (action["status"] != "confirming"
            or action["kind"] not in {"continue", "polish", "inspect", "plan"}
            or payload.get("selection")):
        return None
    saved = conn.execute(
        "SELECT * FROM app_agent_session WHERE project_id=? AND branch_id=? AND request_id=?",
        (*_scope(api), "chat_" + action["id"]),
    ).fetchone()
    if saved is None:
        return None
    plan = _json(saved["plan_json"], {})
    if (saved["chapter_id"] != action["chapter_id"]
            or saved["query"] != action["instruction"].strip()
            or plan.get("mode") != action["kind"] or saved["restored_read_only"]):
        raise ValueError("已保存的写作任务与这条建议不一致，请从全书待办核对。")
    # Recover the committed task before checking a manuscript that may since
    # have changed. Recovery only links its current state; it never resumes it.
    return writing_agent.public_session(api, conn, saved)


def _save_inspiration(api, conn, action, payload):
    proposal = {"kind": "save_inspiration", "label": action["label"],
                "instruction": action["instruction"],
                "title": payload.get("title"), "content": payload.get("content")}
    validate_json(proposal, ACTION_SCHEMA)
    if not proposal["title"].strip() or not proposal["content"].strip():
        raise ValueError("灵感缺少具体标题或内容，请重新讨论后保存。")
    turn = conn.execute(
        "SELECT context_json FROM app_agent_chat_turn WHERE id=? AND project_id=? AND branch_id=?",
        (action["turn_id"], *_scope(api))).fetchone()
    if turn is None:
        raise ValueError("灵感的对话来源缺失，请重新讨论后保存。")
    context = _json(turn["context_json"], {})
    api.validate_material_source(conn, context)
    api.validate_guidance_source(conn, context)
    _validate_project_guidance(api, conn, context)
    record_id, created = api.uid("ent_"), api.now()
    declaration = moyu_records.normalize_scope(
        {"branch_id": api.branch_id(), "epistemic_mode": "author_plan"})
    content = {"type": "灵感", "name": proposal["title"].strip(),
               "description": proposal["content"].strip(), "aliases": [], **declaration}
    conn.execute(
        "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status) "
        "VALUES(?,?,?,?,?,?)",
        (record_id, api.project_id(), content["type"], content["name"], "[]", "active"))
    conn.execute(
        "INSERT INTO app_entity_meta(entity_id,description,updated_at) VALUES(?,?,?)",
        (record_id, content["description"], created))
    moyu_records.write_scope(conn, record_id, declaration, created)
    api.append_event(conn, record_id, "entity.created", content,
                     idempotency_key="chat_inspiration_" + action["id"])
    return {"view": "world", "record_id": record_id, "epistemic_mode": "author_plan"}


def confirm_action(api, conn, action_id, data):
    if data.get("confirm") is not True:
        raise ValueError("请先确认这个创作步骤。")
    _action(api, conn, action_id)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    row = _action(api, conn, action_id)
    if row["status"] == "confirmed":
        return {"action": _public_action(row), "idempotent": True}
    if row["status"] not in {"proposed", "confirming"}:
        raise ValueError("这个步骤已放弃，请重新与总管讨论。")
    payload, source = _json(row["payload_json"], {}), _json(row["source_json"], None)
    result, writing_session, job = {}, None, None
    writing_session = _recover_writing_session(api, conn, row, payload)
    recovered = writing_session is not None
    if recovered:
        result = {"writing_session_id": writing_session["id"], "chapter_id": row["chapter_id"]}
    elif row["kind"] == "open":
        if payload.get("view") not in VIEWS:
            raise ValueError("建议的页面不存在，请重新与总管讨论。")
        result = {"view": payload["view"], "chapter_id": row["chapter_id"]}
    elif row["kind"] == "save_inspiration":
        result = _save_inspiration(api, conn, row, payload)
    else:
        chapter = _assert_source(api, conn, source)
        turn = conn.execute("SELECT context_json FROM app_agent_chat_turn WHERE id=?",
                            (row["turn_id"],)).fetchone()
        context = _json(turn["context_json"], {})
        api.validate_material_source(conn, context)
        api.validate_guidance_source(conn, context)
        _validate_project_guidance(api, conn, context)
        if row["kind"] == "next_chapter":
            result = chapter_transition.complete_and_open_next(
                api, conn, chapter["id"], expected_source=payload.get("source"),
                request_id="chat_" + action_id)
        elif row["kind"] == "polish" and payload.get("selection"):
            canonical = api.candidate_source(conn, "polish", chapter, source)
            selection = {"range": canonical["range"],
                         "revision_id": canonical["source_revision_id"],
                         "chapter_hash": canonical["source_body_hash"]}
            context, _ = api.ai_context(conn, chapter["id"], instruction=row["instruction"],
                                        task="polish", selection=selection)
            context["_source"] = canonical
            job = api.create_ai_job(conn, "polish", context, row["instruction"], "text")
            result = {"job_id": job["id"], "chapter_id": chapter["id"]}
        else:
            # The writing service owns its transaction. Its stable request ID
            # recovers an interrupted confirmation without starting a second task.
            conn.execute("UPDATE app_agent_chat_action SET status='confirming',updated_at=? WHERE id=?",
                         (api.now(), action_id))
            conn.commit()
            outcome = writing_agent.request(api, {
                "request_id": "chat_" + action_id, "chapter_id": chapter["id"],
                "query": row["instruction"], "mode": row["kind"],
            }, expected_source=source)
            writing_session = outcome["session"]
            result = {"writing_session_id": writing_session["id"], "chapter_id": chapter["id"]}
            conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE app_agent_chat_action SET status='confirmed',result_json=?,updated_at=? WHERE id=?",
                 (api.json_text(result), api.now(), action_id))
    conn.commit()
    if job:
        api.start_ai_worker(job["id"])
    return {"action": _public_action(_action(api, conn, action_id)),
            "writing_session": writing_session, "job": job, "idempotent": recovered,
            "recovered": recovered}
