"""Bind the durable writing graph to the existing scoped queue and author receipts."""
from __future__ import annotations

import json
from contextlib import closing

from langgraph.types import Command

import moyu_analysis
import moyu_agent_planning as planning_agent
from moyu_agent_checkpoint import ScopedCheckpointSaver
from moyu_writing_agent import PROSE_MODES, WritingAgentCallbacks, build_graph, initial_state


LABELS = {"continue": "生成正文候选", "polish": "整章修订候选",
          "chapter_analysis": "整理章后记忆", "semantic_review": "故事审稿"}
FINAL = {"review_ready", "canceled", "requires_action", "restored"}


def context_evidence(api, context):
    """Project frozen context metadata without returning prompts or manuscript."""
    current = context.get("current_chapter") or {}
    budget = context.get("context_budget") or {}
    counts = {
        key: len(context.get(key) or [])
        for key in ("previous_chapters", "retrieved_chapters", "confirmed_memory",
                    "story_cards", "author_plans", "reference_passages")
    }
    return {
        "chapter_id": current.get("id", ""),
        "source_revision_id": current.get("source_revision_id") or current.get("revision_id", ""),
        "context_hash": api.hash_text(api.json_text(context)),
        "counts": counts,
        "estimated_tokens": budget.get("estimated_tokens"),
        "max_tokens": budget.get("max_tokens", (budget.get("profile") or {}).get("max_tokens")),
        "retrieval": budget.get("retrieval", ""),
    }


def get_session(api, conn, session_id):
    row = conn.execute(
        "SELECT * FROM app_agent_session WHERE id=? AND project_id=? AND branch_id=?",
        (session_id, api.project_id(), api.branch_id()),
    ).fetchone()
    if row is None:
        raise ValueError("当前作品中没有这条写作任务。")
    return row


def _analysis_receipt(api, conn, job, *, analysis_id=None):
    """Validate a saved analysis against its frozen request and author decisions."""
    scope = {"project_id": api.project_id(), "branch_id": api.branch_id()}
    if (job["project_id"] != scope["project_id"] or job["branch_id"] != scope["branch_id"]
            or job["task"] != "chapter_analysis" or job["status"] != "success"
            or job["cancel_requested"]):
        raise ValueError("本次章后分析尚未完整完成。")
    run = conn.execute(
        "SELECT * FROM ai_run WHERE id=? AND project_id=? AND branch_id=?",
        (job["ai_run_id"], scope["project_id"], scope["branch_id"]),
    ).fetchone()
    result = json.loads(job["result_json"] or "{}")
    if not isinstance(result, dict):
        raise ValueError("本次章后分析缺少完整的确认依据。")
    if result.get("billing_state") not in ("not_dispatched", "usage_reported"):
        raise ValueError("本次任务费用状态无法确认，流程不会自动继续或重复发送")
    artifact = result.get("analysis")
    if (run is None or run["status"] != "success" or not isinstance(artifact, dict)
            or not isinstance(artifact.get("id"), str) or not artifact["id"]
            or (analysis_id is not None and artifact["id"] != analysis_id)
            or result.get("safe_to_apply") is not True or result.get("truncated")
            or result.get("completion_state") not in ("complete", "offline_complete")):
        raise ValueError("本次章后分析缺少完整的确认依据。")
    context = json.loads(job["context_json"])
    consent = json.loads(run["consent_json"] or "{}")
    if context.get("_restored_read_only") or consent.get("restored_read_only"):
        raise ValueError("恢复的章后分析仅供回看，不能继续原任务。")
    # Keep the original request sealed. Only the analysis decision receipt may
    # account for changes made by the author's own confirmation transaction.
    api.moyu_materials.validate_frozen_reference_context(
        context, consent.get("request_audit"),
        input_hash=run["input_hash"], prompt_hash=run["prompt_hash"], require_full_request=True)
    audit = consent.get("request_audit") or {}
    if not isinstance(audit, dict):
        raise ValueError("本次章后分析的完整请求记录缺失。")
    prepared = api.prepare_frozen_ai_request(
        job["task"], job["response_format"], job["max_tokens"],
        consent.get("provider_config") or {}, audit.get("payload"))
    frozen_audit = prepared["request_audit"]
    if (audit.get("endpoint") != prepared["endpoint"]
            or audit.get("request_sha256") != frozen_audit["request_sha256"]
            or audit.get("messages_sha256") != frozen_audit["messages_sha256"]
            or run["prompt_hash"] != frozen_audit["request_sha256"]):
        raise ValueError("本次章后分析的完整请求校验失败，未启动审稿。")
    api.moyu_materials.validate_reference_context(
        conn, scope["project_id"], scope["branch_id"], context)
    item = next((item for item in moyu_analysis.list_analysis(conn, scope, job["chapter_id"])
                 if item["id"] == artifact["id"]), None)
    source = context.get("_source") or {}
    if (item is None or item.get("stale") or item.get("status") == "stale"
            or item.get("chapter_id") != job["chapter_id"]
            or item.get("source_revision_id") != source.get("source_revision_id")
            or item.get("chapter_hash") != source.get("source_body_hash")
            or item.get("material_source") != context.get("_material_source")
            or item.get("guidance_source") != context.get("_guidance_source")):
        raise ValueError("本次章后分析的原稿、资料或创作约定已变化，请重新检查当前章节。")
    chapter = next((chapter for chapter in api.chapter_rows(conn)
                    if chapter["id"] == job["chapter_id"]), None)
    api.candidate_source(conn, "chapter_analysis", chapter, source)
    return item


def public_session(api, conn, row):
    plan = json.loads(row["plan_json"])
    if plan.get("mode") == "plan":
        return planning_agent.public_session(api, conn, row)
    summary = plan.get("summary", {})
    restored = bool(row["restored_read_only"])
    steps = []
    current_job = None
    analysis_job = None
    evidence = {} if restored else plan.get("context_evidence", {})
    result_refs = {}
    mode = plan.get("mode", "inspect")
    for task in ([mode] if mode in PROSE_MODES else []) + ["chapter_analysis", "semantic_review"]:
        ref = plan.get("steps", {}).get(task, {})
        job = None if restored else conn.execute(
            "SELECT * FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
            (ref.get("job_id", ""), row["project_id"], row["branch_id"]),
        ).fetchone()
        item = {"task": task, "label": LABELS[task], "status": job["status"] if job else "pending"}
        if job:
            if task == "chapter_analysis":
                analysis_job = job
            item.update(job_id=job["id"], ai_run_id=job["ai_run_id"])
            result = json.loads(job["result_json"] or "{}")
            result_key = {"chapter_analysis": "analysis", "semantic_review": "story_review"}.get(task)
            artifact = result.get(result_key) if result_key else None
            if isinstance(artifact, dict) and artifact.get("id"):
                result_refs[task] = {"id": artifact["id"], "ai_run_id": job["ai_run_id"]}
            if job["id"] == summary.get("current_job_id"):
                current_job = api.ai_job_public(job, conn=conn)
                evidence = context_evidence(api, json.loads(job["context_json"] or "{}"))
        steps.append(item)
    expected = plan.get("adopted_source", plan.get("source", {}))
    current = conn.execute(
        "SELECT r.id FROM document_node n JOIN document_revision r "
        "ON r.id=(SELECT id FROM document_revision WHERE document_node_id=n.id ORDER BY rowid DESC LIMIT 1) "
        "WHERE n.id=? AND n.project_id=? AND n.branch_id=? "
        "AND NOT EXISTS (SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id)",
        (row["chapter_id"], row["project_id"], row["branch_id"]),
    ).fetchone()
    source_stale = restored or current is None or current["id"] != expected.get("source_revision_id")
    if row["status"] == "awaiting_memory" and not restored:
        try:
            if analysis_job is None:
                raise ValueError("章后分析记录缺失。")
            _analysis_receipt(api, conn, analysis_job, analysis_id=summary.get("analysis_id"))
        except ValueError:
            source_stale = True
    return {"id": row["id"], "chapter_id": row["chapter_id"], "query": row["query"],
            "mode": plan.get("mode", "inspect"), "status": row["status"],
            "stage": summary.get("stage", "preflight"), "calls_used": summary.get("calls_used", 0),
            "max_calls": 3 if mode in PROSE_MODES else 2,
            "checkpoint_id": plan.get("checkpoint_id", "") if not restored else "",
            "steps": steps, "current_job": current_job,
            "context_evidence": evidence, "result_refs": result_refs, "source_stale": source_stale,
            "error": summary.get("error", ""), "restored_read_only": restored,
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _validate_expected_source(api, conn, chapter_id, source):
    if source is None:
        return
    chapter = next((c for c in api.chapter_rows(conn) if c["id"] == chapter_id), None)
    if chapter is None or any(source.get(key) != value for key, value in (
        ("project_id", api.project_id()), ("branch_id", api.branch_id()),
        ("chapter_id", chapter_id), ("source_revision_id", chapter["revision_id"]),
        ("source_body_hash", api.hash_text(chapter["body"])),
    )):
        raise ValueError("原稿已变化，请按当前正文重新讨论。")


def _planning_request(api, data, session_id=None, *, expected_source=None):
    pending, canceled = [], []
    with closing(api.connect(check_same_thread=False)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if session_id is None:
            request_id = api.checked_id(data.get("request_id", ""), "request id")
            chapter_id = api.checked_id(data.get("chapter_id", ""), "chapter id")
            query = data.get("query", "")
            if not isinstance(query, str) or not query.strip() or len(query) > 2000:
                raise ValueError("请填写 1 至 2000 字的规划要求。")
            query = query.strip()
            previous = conn.execute(
                "SELECT * FROM app_agent_session WHERE project_id=? AND branch_id=? AND request_id=?",
                (api.project_id(), api.branch_id(), request_id),
            ).fetchone()
            if previous:
                if (previous["chapter_id"] != chapter_id or previous["query"] != query or
                        json.loads(previous["plan_json"]).get("mode") != "plan"):
                    raise ValueError("这次请求已保存，请回看原任务或创建新任务。")
                return {"session": public_session(api, conn, previous), "idempotent": True}
            _validate_expected_source(api, conn, chapter_id, expected_source)
            plan = planning_agent.create(api, conn, chapter_id, query)
            session_id = api.uid("agent_")
            conn.execute("INSERT INTO app_agent_session VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                         (session_id, api.project_id(), api.branch_id(), chapter_id, request_id,
                          query, api.json_text(plan), plan["summary"]["status"], api.now(), api.now()))
            idempotent = False
        else:
            row = get_session(api, conn, session_id)
            original = json.loads(row["plan_json"])
            outcome = planning_agent.resume(api, conn, row, data)
            plan, pending, canceled = outcome["plan"], outcome["pending"], outcome["canceled"]
            idempotent = plan == original
            if not idempotent:
                conn.execute("UPDATE app_agent_session SET status=?,plan_json=?,updated_at=? WHERE id=?",
                             (plan["summary"]["status"], api.json_text(plan), api.now(), session_id))
        if not idempotent:
            api.append_event(conn, session_id, "writing_agent.progress",
                             {"status": plan["summary"]["status"], "stage": plan["summary"]["stage"]})
        result = {"session": public_session(api, conn, get_session(api, conn, session_id)), "idempotent": idempotent}
        conn.commit()
    for job_id in canceled:
        api.cancel_ai_job(job_id)
    for job_id in pending:
        api.start_ai_worker(job_id)
    return result


def request(api, data, session_id=None, *, expected_source=None):
    """Commit graph + queue atomically; only then dispatch queued work."""
    if not isinstance(data, dict):
        raise ValueError("写作任务内容无效。")
    allowed = {"action", "checkpoint_id"} if session_id else {"request_id", "chapter_id", "query", "mode"}
    if set(data) - allowed:
        raise ValueError("写作任务包含不支持的选项。")
    if session_id is None and data.get("mode") == "plan":
        return _planning_request(api, data, expected_source=expected_source)
    if session_id is not None:
        session_id = api.checked_id(session_id, "writing session")
        with closing(api.connect()) as conn:
            row = get_session(api, conn, session_id)
            planning = json.loads(row["plan_json"]).get("mode") == "plan"
        if planning:
            return _planning_request(api, data, session_id)
    pending, canceled = [], []
    with closing(api.connect(check_same_thread=False)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if session_id is None:
            request_id = api.checked_id(data.get("request_id", ""), "request id")
            chapter_id = api.checked_id(data.get("chapter_id", ""), "chapter id")
            query = data.get("query", "")
            if not isinstance(query, str) or not query.strip() or len(query) > 2000:
                raise ValueError("请填写 1 至 2000 字的创作要求。")
            query = query.strip()
            mode = data.get("mode", "continue")
            if not isinstance(mode, str) or mode not in PROSE_MODES | {"inspect"}:
                raise ValueError("请选择续写、整章修订或检查当前章节。")
            previous = conn.execute(
                "SELECT * FROM app_agent_session WHERE project_id=? AND branch_id=? AND request_id=?",
                (api.project_id(), api.branch_id(), request_id),
            ).fetchone()
            if previous:
                if (previous["chapter_id"] != chapter_id or previous["query"] != query or
                        json.loads(previous["plan_json"]).get("mode") != mode):
                    raise ValueError("这次请求已保存，请回看原任务或创建新任务。")
                return {"session": public_session(api, conn, previous), "idempotent": True}
            _validate_expected_source(api, conn, chapter_id, expected_source)
            chapter = next((c for c in api.chapter_rows(conn) if c["id"] == chapter_id), None)
            if chapter is None:
                raise ValueError("请先选择当前作品中的章节。")
            if mode in {"inspect", "polish"} and not chapter["body"].strip():
                raise ValueError("这一章还没有正文，请先写下内容，或选择续写并检查。")
            if mode == "polish" and len(chapter["body"]) > 12000:
                raise ValueError("这一章超过 12000 字，请先在正文中选中要修订的段落，再使用局部修订。")
            first_task = mode if mode in PROSE_MODES else "chapter_analysis"
            source = api.candidate_source(conn, first_task, chapter)
            selection = {"range": source["range"], "revision_id": source["source_revision_id"],
                         "chapter_hash": source["source_body_hash"]} if mode == "polish" else None
            context, _ = api.ai_context(conn, chapter_id, instruction=query, task=first_task,
                                       selection=selection)
            session_id = api.uid("agent_")
            plan = {"mode": mode, "steps": {}, "source": source,
                    "context_hash": api.hash_text(api.json_text(context)),
                    "context_evidence": context_evidence(api, context),
                    "provider": {k: v for k, v in api.ai_settings(conn).items() if k != "api_key"}}
            conn.execute("INSERT INTO app_agent_session VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                         (session_id, api.project_id(), api.branch_id(), chapter_id, request_id,
                          query, api.json_text(plan), "planned", api.now(), api.now()))
            row = get_session(api, conn, session_id)
            starting = True
        else:
            session_id = api.checked_id(session_id, "writing session")
            row = get_session(api, conn, session_id)
            if row["restored_read_only"]:
                raise ValueError("备份恢复的写作任务仅供回看，请按当前作品创建新任务。")
            plan = json.loads(row["plan_json"])
            action = data.get("action")
            if not isinstance(action, str) or action not in {"authorize", "advance", "applied", "cancel"}:
                raise ValueError("写作任务操作无效。")
            if not isinstance(data.get("checkpoint_id"), str) or not data["checkpoint_id"]:
                raise ValueError("任务进度缺失，请刷新后继续。")
            if data["checkpoint_id"] != plan.get("checkpoint_id") or row["status"] in FINAL:
                return {"session": public_session(api, conn, row), "idempotent": True}
            if action == "cancel":
                for step in plan.get("steps", {}).values():
                    job = conn.execute("SELECT * FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
                                       (step["job_id"], api.project_id(), api.branch_id())).fetchone()
                    if job and job["status"] == "queued":
                        conn.execute("UPDATE app_ai_job SET cancel_requested=1 WHERE id=?", (job["id"],))
                        api.finish_ai_job(conn, job, {"billing_state": "not_dispatched"}, None, 0)
                    elif job and job["status"] == "running":
                        conn.execute("UPDATE app_ai_job SET cancel_requested=1,updated_at=? WHERE id=?",
                                     (api.now(), job["id"]))
                        canceled.append(job["id"])
                plan.setdefault("summary", {})["status"] = "canceled"
                conn.execute("UPDATE app_agent_session SET status='canceled',plan_json=?,updated_at=? WHERE id=?",
                             (api.json_text(plan), api.now(), session_id))
                result = {"session": public_session(api, conn, get_session(api, conn, session_id)), "idempotent": False}
                conn.commit()
                for job_id in canceled:
                    api.cancel_ai_job(job_id)
                return result
            expected = {"awaiting_authorization": "authorize", "awaiting_job": "advance",
                        "awaiting_adoption": "applied", "awaiting_memory": "advance"}
            if expected.get(row["status"]) != action:
                raise ValueError("任务进度已变化，请按当前步骤继续。")
            starting = False

        scope = {"project_id": api.project_id(), "branch_id": api.branch_id()}
        saver = ScopedCheckpointSaver(conn, scope, session_id)

        def prepare(state):
            return {"status": "ready", "source_revision_id": plan["source"]["source_revision_id"],
                    "source_hash": plan["source"]["source_body_hash"]}

        def enqueue(state, task, step_id):
            with saver.lock:
                if task in plan["steps"]:
                    saved = plan["steps"][task]
                    if saved["step_id"] != step_id:
                        raise ValueError("任务步骤身份不一致，请回看任务记录。")
                    return dict(conn.execute("SELECT * FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
                                             (saved["job_id"], scope["project_id"], scope["branch_id"])).fetchone())
                chapter = next((c for c in api.chapter_rows(conn) if c["id"] == row["chapter_id"]), None)
                expected_source = plan.get("adopted_source", plan["source"])
                if (chapter is None or chapter["revision_id"] != expected_source["source_revision_id"] or
                        api.hash_text(chapter["body"]) != expected_source["source_body_hash"]):
                    raise ValueError("原稿已变化，请按当前章节创建新任务。")
                config = api.ai_settings(conn)
                if {k: v for k, v in config.items() if k != "api_key"} != plan["provider"]:
                    raise ValueError("模型设置已变化，请重新查看计划后启动。")
                source = api.candidate_source(conn, task, chapter)
                selection = {"range": source["range"], "revision_id": source["source_revision_id"],
                             "chapter_hash": source["source_body_hash"]} if task == "polish" else None
                context, _ = api.ai_context(conn, row["chapter_id"], instruction=row["query"], task=task,
                                           selection=selection)
                if not plan["steps"] and api.hash_text(api.json_text(context)) != plan["context_hash"]:
                    raise ValueError("创作资料已变化，请按当前资料重新查看计划。")
                if plan.get("followup_materials"):
                    api.moyu_materials.validate(conn, scope["project_id"], scope["branch_id"], plan["followup_materials"])
                context["_source"] = source
                context["_writing_agent"] = {"session_id": session_id, "step_id": step_id}
                followup_materials = context["_material_source"]
                # A failed budget reservation must not leave an untracked queued run.
                conn.execute("SAVEPOINT agent_enqueue")
                try:
                    job = api.create_ai_job(conn, task, context, row["query"], api.ai_contract(task)["response_format"],
                                            provider_config=config)
                    saved_step = {"step_id": step_id, "job_id": job["id"], "ai_run_id": job["ai_run_id"]}
                except Exception:
                    conn.execute("ROLLBACK TO agent_enqueue")
                    conn.execute("RELEASE agent_enqueue")
                    raise
                conn.execute("RELEASE agent_enqueue")
                plan["steps"][task] = saved_step
                plan["followup_materials"] = followup_materials
                pending.append(job["id"])
                return job

        def get_job(state, job_id):
            with saver.lock:
                ref = plan["steps"].get(state["current_task"], {})
                if ref.get("job_id") != job_id:
                    raise ValueError("任务不属于本次写作流程。")
                job = conn.execute("SELECT * FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
                                   (job_id, scope["project_id"], scope["branch_id"])).fetchone()
                if job is None:
                    raise ValueError("已保存的任务记录缺失，未重新生成。")
                value = api.ai_job_public(job, conn=conn)
                run = conn.execute("SELECT status FROM ai_run WHERE id=?", (job["ai_run_id"],)).fetchone()
                if run["status"] == "unknown":
                    value["status"] = "unknown"
                if value["status"] == "success":
                    context = json.loads(job["context_json"])
                    receipt = conn.execute("SELECT 1 FROM app_candidate_application WHERE ai_run_id=? AND project_id=? AND branch_id=?",
                                           (job["ai_run_id"], scope["project_id"], scope["branch_id"])).fetchone()
                    if job["task"] in PROSE_MODES and receipt:
                        # Adoption may commit before the worker's completion callback.
                        verify_adoption(state, {})
                        value["materials_read_only"] = False
                        if isinstance(value.get("result"), dict):
                            value["result"]["materials_read_only"] = False
                    elif job["task"] == "chapter_analysis":
                        _analysis_receipt(api, conn, job)
                        value["materials_read_only"] = False
                        value.pop("material_issue", None)
                        if isinstance(value.get("result"), dict):
                            value["result"]["materials_read_only"] = False
                            value["result"].pop("material_issue", None)
                    else:
                        api.validate_material_source(conn, context, run_id=job["ai_run_id"])
                        api.validate_guidance_source(conn, context)
                        chapter = next((c for c in api.chapter_rows(conn) if c["id"] == row["chapter_id"]), None)
                        api.candidate_source(conn, job["task"], chapter, context["_source"])
                return value

        def verify_adoption(state, answer):
            with saver.lock:
                receipt = conn.execute(
                    "SELECT * FROM app_candidate_application WHERE ai_run_id=? AND project_id=? AND branch_id=? AND chapter_id=?",
                    (state["current_ai_run_id"], scope["project_id"], scope["branch_id"], row["chapter_id"]),
                ).fetchone()
                chapter = next((c for c in api.chapter_rows(conn) if c["id"] == row["chapter_id"]), None)
                if not receipt or not chapter or receipt["result_revision_id"] != chapter["revision_id"]:
                    raise ValueError("请先采纳本次正文候选；已改动的正文需要创建新任务。")
                plan["adopted_source"] = api.candidate_source(conn, "chapter_analysis", chapter)
                plan.pop("followup_materials", None)
                return {"applied": True, "ai_run_id": state["current_ai_run_id"], "result_ref": receipt["result_revision_id"],
                        "source_revision_id": chapter["revision_id"], "source_hash": api.hash_text(chapter["body"])}

        def verify_memory(state):
            with saver.lock:
                ref = plan["steps"].get("chapter_analysis", {})
                if (ref.get("job_id") != state.get("current_job_id")
                        or ref.get("ai_run_id") != state.get("current_ai_run_id")):
                    raise ValueError("章后资料不属于本次写作任务。")
                job = conn.execute(
                    "SELECT * FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=? AND chapter_id=?",
                    (ref["job_id"], scope["project_id"], scope["branch_id"], row["chapter_id"]),
                ).fetchone()
                if job is None:
                    raise ValueError("本次章后分析记录缺失，未重新生成。")
                item = _analysis_receipt(api, conn, job, analysis_id=state.get("analysis_id"))
                ready = (item.get("status") == "reviewed"
                         and all(candidate.get("status") in {"accepted", "rejected"}
                                 for candidate in item["candidates"])
                         and all(item[name].get("status") in {"accepted", "rejected", "empty"}
                                 for name in ("summary", "handoff")))
                if ready:
                    plan["followup_materials"] = item.get("decision_material_source", item["material_source"])
                return {"analysis_id": item["id"], "ready": ready}

        # Reject premature adoption outside the graph, preserving its resumable interrupt.
        if not starting and data["action"] == "applied":
            verify_adoption(plan["summary"], {})
        callbacks = WritingAgentCallbacks(
            prepare, enqueue, get_job, verify_adoption, verify_memory=verify_memory)
        graph = build_graph(callbacks, saver)
        config = {"configurable": {"thread_id": saver.thread_id}}
        value = (initial_state(session_id, scope["project_id"], scope["branch_id"], row["chapter_id"],
                               plan["mode"], max_calls=3 if plan["mode"] in PROSE_MODES else 2)
                 if starting else Command(resume={"action": data["action"]}))
        graph.invoke(value, config, durability="sync")
        snapshot = graph.get_state(config)
        summary = dict(snapshot.values)
        if snapshot.next == ("adopt_continue",):
            summary["status"] = "awaiting_adoption"
        plan["summary"] = summary
        plan["checkpoint_id"] = snapshot.config["configurable"]["checkpoint_id"]
        conn.execute("UPDATE app_agent_session SET status=?,plan_json=?,updated_at=? WHERE id=?",
                     (summary["status"], api.json_text(plan), api.now(), session_id))
        api.append_event(conn, session_id, "writing_agent.progress", {"status": summary["status"], "stage": summary["stage"]})
        result = {"session": public_session(api, conn, get_session(api, conn, session_id)), "idempotent": False}
        conn.commit()
    for job_id in pending:
        api.start_ai_worker(job_id)
    return result


def advance_ready(api, *, job_id=None, run_id=None, analysis_id=None):
    """Called after committed worker/author events, never from a read endpoint."""
    with closing(api.connect()) as conn:
        rows = conn.execute("SELECT * FROM app_agent_session WHERE project_id=? AND branch_id=? "
                            "AND restored_read_only=0 AND status IN ('awaiting_job','awaiting_adoption','awaiting_memory')",
                            (api.project_id(), api.branch_id())).fetchall()
        ready = []
        for row in rows:
            plan = json.loads(row["plan_json"])
            state = plan.get("summary", {})
            if job_id and state.get("current_job_id") != job_id:
                continue
            if run_id and state.get("current_ai_run_id") != run_id:
                continue
            if analysis_id:
                ref = plan.get("steps", {}).get("chapter_analysis", {})
                job = conn.execute(
                    "SELECT result_json FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
                    (ref.get("job_id"), api.project_id(), api.branch_id()),
                ).fetchone()
                result = json.loads(job["result_json"] or "{}") if job else {}
                if (result.get("analysis") or {}).get("id") != analysis_id:
                    continue
            if row["status"] == "awaiting_adoption":
                if not conn.execute("SELECT 1 FROM app_candidate_application WHERE ai_run_id=? AND project_id=? AND branch_id=?",
                                    (state.get("current_ai_run_id"), api.project_id(), api.branch_id())).fetchone():
                    continue
                action = "applied"
            else:
                job = conn.execute("SELECT status FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
                                   (state.get("current_job_id"), api.project_id(), api.branch_id())).fetchone()
                if job and job["status"] in {"queued", "running"}:
                    continue
                if row["status"] == "awaiting_memory":
                    # An author event or restart may resume completed decisions.
                    # Reads never reach this path, and partial saves stay paused.
                    stored = conn.execute(
                        "SELECT payload_json FROM app_chapter_analysis WHERE id=? AND project_id=? AND branch_id=?",
                        (state.get("analysis_id"), api.project_id(), api.branch_id()),
                    ).fetchone()
                    if stored and json.loads(stored["payload_json"]).get("status") != "reviewed":
                        continue
                action = "advance"
            ready.append((row["id"], {"action": action, "checkpoint_id": plan.get("checkpoint_id")}))
    for session_id, data in ready:
        try:
            result = request(api, data, session_id)["session"]
            if result["status"] == "awaiting_adoption":
                with closing(api.connect()) as conn:
                    receipt = conn.execute("SELECT 1 FROM app_candidate_application WHERE ai_run_id=? AND project_id=? AND branch_id=?",
                                           (result["current_job"]["ai_run_id"], api.project_id(), api.branch_id())).fetchone()
                if receipt:
                    request(api, {"action": "applied", "checkpoint_id": result["checkpoint_id"]}, session_id)
            elif result["status"] == "awaiting_memory":
                with closing(api.connect()) as conn:
                    ref = result.get("result_refs", {}).get("chapter_analysis", {})
                    item = conn.execute(
                        "SELECT payload_json FROM app_chapter_analysis WHERE id=? AND project_id=? AND branch_id=?",
                        (ref.get("id"), api.project_id(), api.branch_id()),
                    ).fetchone()
                if item and json.loads(item["payload_json"]).get("status") == "reviewed":
                    request(api, {"action": "advance", "checkpoint_id": result["checkpoint_id"]}, session_id)
        except Exception:
            # Keep the committed checkpoint available for an explicit UI retry.
            api.sys.stderr.write("Writing agent continuation deferred; progress is saved.\n")
