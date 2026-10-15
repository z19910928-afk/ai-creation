"""Connect the writing manager to the existing, author-confirmed planning engine."""
from __future__ import annotations

from copy import deepcopy
import json


LABELS = {"overview": "故事总纲", "volumes": "分卷规划", "chapters": "章节规划", "scenes": "场景规划"}
DEFAULT_COUNTS = {"volumes": 1, "chapters_per_volume": 3, "scenes_per_chapter": 2}
COUNT_LIMITS = {"volumes": 3, "chapters_per_volume": 8, "scenes_per_chapter": 4}
FINAL = {"review_ready", "canceled", "requires_action", "restored"}


def _scope(api, row):
    if row["project_id"] != api.project_id() or row["branch_id"] != api.branch_id():
        raise ValueError("当前作品中没有这条规划任务。")


def _provider(api, conn):
    return {key: value for key, value in api.ai_settings(conn).items() if key != "api_key"}


def _source(api, current):
    return {"blueprint_version": current["version"],
            "blueprint_hash": api.planning_hash({"version": current["version"], "blueprint": current["blueprint"]})}


def _counts(blueprint):
    volumes = blueprint.get("volumes") or []
    chapters = [chapter for volume in volumes for chapter in volume.get("chapters") or []]
    inferred = {
        "volumes": len(volumes),
        "chapters_per_volume": max((len(volume.get("chapters") or []) for volume in volumes), default=0),
        "scenes_per_chapter": max((len(chapter.get("scenes") or []) for chapter in chapters), default=0),
    }
    saved = blueprint.get("counts") or {}
    result = {}
    for key, default in DEFAULT_COUNTS.items():
        value = saved.get(key, inferred[key]) if isinstance(saved, dict) else inferred[key]
        result[key] = min(value, COUNT_LIMITS[key]) if type(value) is int and value > 0 else default
    return result


def create(api, conn, chapter_id, query):
    """Save a bounded planning workflow without inserting a session or a job."""
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("请填写 1 至 2000 字的规划要求。")
    if not any(chapter["id"] == chapter_id for chapter in api.chapter_rows(conn)):
        raise ValueError("请先选择当前作品中的章节。")
    current = api.workspace.blueprint_row(conn, api.project_id(), api.branch_id())
    blueprint = current["blueprint"]
    initial = {key: blueprint.get(key, "") for key in ("genre", "tone", "audience", "contract")}
    initial.update(premise=query.strip(), counts=_counts(blueprint))
    workflow = api.planning.create_workflow(
        conn, api.project_id(), api.branch_id(), initial,
        {"source_version": current["version"], "callbacks": api.planning_callbacks()})
    return {
        "mode": "plan", "planning_workflow_id": workflow["id"], "planning_version": workflow["version"],
        "provider": _provider(api, conn), "source": _source(api, current), "steps": {},
        "checkpoint_id": api.uid("agp_"),
        "summary": {"status": "awaiting_authorization", "stage": "overview", "calls_used": 0,
                    "current_job_id": "", "current_ai_run_id": "", "error": ""},
    }


def _callbacks(api, plan):
    callbacks = dict(api.planning_callbacks())
    get_job = callbacks["get_job"]

    def guarded_job(**kwargs):
        raw = get_job(**kwargs)
        if not isinstance(raw, dict):
            raise ValueError("规划任务记录损坏，未重新发送。")
        if raw.get("id") not in (None, kwargs["job_id"]):
            raise ValueError("规划任务身份已变化，未重新发送。")
        if raw.get("status") not in {"success", "complete", "succeeded", "done"}:
            return raw
        result = raw.get("result") or {}
        if not isinstance(result, dict) or result.get("billing_state") not in ("not_dispatched", "usage_reported"):
            return {**raw, "status": "unknown", "error": "本次任务费用状态无法确认，规划不会自动继续或重复发送。"}
        if (raw.get("restored_read_only") or raw.get("materials_read_only")
                or result.get("restored_read_only") or result.get("materials_read_only")
                or result.get("truncated")):
            return {**raw, "status": "failed", "error": "规划候选的来源或完整性已不能用于后续流程。"}
        return raw

    callbacks["get_job"] = guarded_job
    return callbacks


def _record(api, conn, plan, workflow):
    attempt = workflow.get("attempt")
    summary = plan["summary"]
    plan["planning_version"] = workflow["version"]
    summary["stage"] = workflow["stage"]
    if attempt and attempt.get("job_id"):
        job = conn.execute("SELECT * FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
                           (attempt["job_id"], api.project_id(), api.branch_id())).fetchone()
        if job is None:
            raise ValueError("原规划任务未找到，未重新发送。")
        stage = workflow["stage"]
        previous = plan["steps"].get(stage)
        if previous and previous["job_id"] != job["id"]:
            raise ValueError("规划阶段已由其他操作改变，未重复发送。")
        binding = json.loads(job["context_json"] or "{}").get("planning_workflow") or {}
        if (binding.get("id") != workflow["id"] or binding.get("stage") != stage
                or binding.get("attempt_id") != attempt["id"] or attempt["attempt_no"] != 1):
            raise ValueError("规划任务与阶段不一致，未重复发送。")
        plan["steps"][stage] = {
            "step_id": attempt["id"], "job_id": job["id"], "ai_run_id": job["ai_run_id"],
            "status": attempt["status"], "candidate_hash": attempt.get("candidate_hash"),
        }
        summary.update(current_job_id=job["id"], current_ai_run_id=job["ai_run_id"])
    summary["calls_used"] = len(plan["steps"])


def _validate(api, conn, plan, workflow):
    if _provider(api, conn) != plan["provider"]:
        raise ValueError("模型设置已变化，请重新查看规划计划后启动。")
    current = api.workspace.blueprint_row(conn, api.project_id(), api.branch_id())
    if _source(api, current) != plan.get("applied_source", plan["source"]):
        raise ValueError("创作蓝图已变化，请按当前蓝图创建新规划；原候选继续保留。")
    if workflow["version"] != plan["planning_version"]:
        raise ValueError("规划阶段已由其他操作改变，请回看现有规划。")


def _start(api, conn, plan, workflow, callbacks, pending):
    if len(plan["steps"]) >= 4 or workflow["stage"] in plan["steps"]:
        raise ValueError("本次规划已达到四阶段调用上限，未重复发送。")
    workflow = api.planning.start_stage(
        conn, workflow["id"], callbacks, expected_stage=workflow["stage"])
    _record(api, conn, plan, workflow)
    plan["summary"].update(status="awaiting_job", error="")
    pending.append(workflow["attempt"]["job_id"])
    return workflow


def resume(api, conn, row, data):
    """Return changes for the caller to commit before starting/canceling workers."""
    _scope(api, row)
    if row["restored_read_only"]:
        raise ValueError("备份恢复的规划仅供回看，请按当前作品创建新任务。")
    original = json.loads(row["plan_json"])
    if original.get("mode") != "plan":
        raise ValueError("这条任务不是创作规划。")
    if not isinstance(data, dict) or set(data) - {"action", "checkpoint_id"}:
        raise ValueError("规划任务操作无效。")
    action = data.get("action")
    if not isinstance(action, str) or action not in {"authorize", "advance", "confirm", "apply", "cancel"}:
        raise ValueError("规划任务操作无效。")
    if not isinstance(data.get("checkpoint_id"), str) or not data["checkpoint_id"]:
        raise ValueError("任务进度缺失，请刷新后继续。")
    result = {"plan": deepcopy(original), "pending": [], "canceled": []}
    if data["checkpoint_id"] != original["checkpoint_id"] or row["status"] in FINAL:
        return result
    plan, summary = result["plan"], result["plan"]["summary"]
    expected = {"awaiting_authorization": "authorize", "awaiting_job": "advance",
                "awaiting_plan_confirmation": "confirm", "awaiting_plan_application": "apply"}
    if action == "advance" and row["status"] != "awaiting_job":
        return result
    if action != "cancel" and expected.get(row["status"]) != action:
        raise ValueError("任务进度已变化，请按当前步骤继续。")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("SAVEPOINT agent_planning_resume")
    try:
        workflow = api.planning.get_workflow(
            conn, plan["planning_workflow_id"], api.project_id(), api.branch_id())
        callbacks = _callbacks(api, plan)
        if action == "cancel":
            attempt = workflow.get("attempt") or {}
            if workflow["status"] == "active":
                workflow = api.planning.cancel_workflow(conn, workflow["id"], callbacks, "作者停止总管规划")
            if attempt.get("job_id") and attempt["status"] in {"queued", "running", "unknown"}:
                result["canceled"].append(attempt["job_id"])
            _record(api, conn, plan, workflow)
            summary.update(status="canceled", error="")
        else:
            _validate(api, conn, plan, workflow)
            if action == "authorize":
                _start(api, conn, plan, workflow, callbacks, result["pending"])
            elif action == "advance":
                workflow = api.planning.refresh(conn, workflow["id"], callbacks)
                _record(api, conn, plan, workflow)
                attempt = workflow.get("attempt") or {}
                if attempt.get("status") == "complete":
                    summary.update(status="awaiting_plan_confirmation", error="")
                elif attempt.get("status") in {"failed", "unknown", "canceled"}:
                    summary.update(status="requires_action", error=attempt.get("error") or "规划任务未成功完成，未重复发送。")
            elif action == "confirm":
                attempt = workflow.get("attempt") or {}
                saved = plan["steps"].get(workflow["stage"], {})
                workflow = api.planning.confirm_stage(
                    conn, workflow["id"], callbacks, expected_stage=workflow["stage"],
                    expected_version=plan["planning_version"], attempt_id=saved.get("step_id"),
                    candidate_hash=saved.get("candidate_hash"))
                plan["steps"][attempt["stage"]]["status"] = "confirmed"
                _record(api, conn, plan, workflow)
                if workflow["status"] == "completed":
                    summary.update(status="awaiting_plan_application", error="")
                else:
                    _start(api, conn, plan, workflow, callbacks, result["pending"])
            elif action == "apply":
                applied = api.apply_planning_workflow(
                    conn, workflow["id"], {"expected_source_version": workflow["source_version"]})
                if applied.get("applied") is not True:
                    raise ValueError("完整规划尚未采用。")
                plan["applied_source"] = _source(api, applied)
                summary.update(status="review_ready", error="")
    except (ValueError, RuntimeError) as exc:
        conn.execute("ROLLBACK TO agent_planning_resume")
        result = {"plan": deepcopy(original), "pending": [], "canceled": []}
        result["plan"]["summary"].update(status="requires_action", error=str(exc)[:2048])
    finally:
        conn.execute("RELEASE agent_planning_resume")
    if result["plan"] != original:
        result["plan"]["checkpoint_id"] = api.uid("agp_")
    return result


def public_session(api, conn, row):
    """Read the existing planning preview without refreshing or dispatching."""
    _scope(api, row)
    plan = json.loads(row["plan_json"])
    summary = plan.get("summary") or {}
    restored = bool(row["restored_read_only"])
    workflow, current_job = None, None
    source_stale = True
    steps = []
    if not restored:
        workflow = api.planning_public(conn, api.planning.get_workflow(
            conn, plan["planning_workflow_id"], api.project_id(), api.branch_id()))
        current = api.workspace.blueprint_row(conn, api.project_id(), api.branch_id())
        source_stale = _source(api, current) != plan.get("applied_source", plan["source"])
    for stage, label in LABELS.items():
        ref = plan.get("steps", {}).get(stage) or {}
        item = {"task": stage, "label": label, "status": ref.get("status", "pending")}
        if not restored and ref.get("job_id"):
            job = conn.execute("SELECT * FROM app_ai_job WHERE id=? AND project_id=? AND branch_id=?",
                               (ref["job_id"], row["project_id"], row["branch_id"])).fetchone()
            if job:
                item.update(job_id=job["id"], ai_run_id=job["ai_run_id"])
                if job["id"] == summary.get("current_job_id"):
                    current_job = api.ai_job_public(job, conn=conn)
        steps.append(item)
    return {
        "id": row["id"], "mode": "plan", "chapter_id": row["chapter_id"], "query": row["query"],
        "status": row["status"], "stage": summary.get("stage", "overview"),
        "calls_used": summary.get("calls_used", 0), "max_calls": 4,
        "checkpoint_id": "" if restored else plan["checkpoint_id"],
        "steps": steps, "current_job": current_job, "planning_workflow": workflow,
        "source_stale": source_stale, "error": summary.get("error", ""),
        "restored_read_only": restored, "result_refs": {}, "context_evidence": {},
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
