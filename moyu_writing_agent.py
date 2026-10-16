"""Durable LangGraph writing-agent state machine.

The graph is deliberately a small coordinator, not a second AI queue.  It
stores only scope identifiers, source hashes, job identities, and progress.
All manuscript/material reads, job creation, candidate application, and
analysis persistence are supplied by trusted callbacks owned by ``server.py``.

The module has no provider, thread, or database side effects of its own.
A caller binds one graph/checkpointer to a trusted workflow scope and uses
that workflow's stable ``thread_id`` for every invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, TypedDict

try:  # LangGraph is an optional runtime dependency managed by the host app.
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
except ImportError:  # pragma: no cover - exercised only before dependency install.
    END = "__end__"
    START = "__start__"
    StateGraph = None
    Command = None

    def interrupt(_value: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("LangGraph is not installed; writing agent is unavailable")


class WritingAgentDependencyError(RuntimeError):
    """Raised when the LangGraph runtime is not available."""


class WritingAgentContractError(ValueError):
    """Raised for malformed callback results or unsafe state."""


class WritingAgentState(TypedDict, total=False):
    """Small checkpoint-safe state; never place manuscript text or prompts here."""

    workflow_id: str
    project_id: str
    branch_id: str
    chapter_id: str
    mode: str
    status: str
    stage: str
    step_no: int
    max_calls: int
    calls_used: int
    authorized: bool
    source_revision_id: str
    source_hash: str
    current_step_id: str
    current_task: str
    current_job_id: str
    current_ai_run_id: str
    current_candidate_hash: str
    analysis_id: str
    adoption_run_id: str
    last_result_ref: str
    preflight_status: str
    error: str


SAFE_STATUS = {
    "planned",
    "running",
    "awaiting_authorization",
    "awaiting_job",
    "awaiting_adoption",
    "awaiting_memory",
    "requires_action",
    "review_ready",
    "canceled",
}
PROSE_MODES = {"continue", "polish"}
SAFE_MODES = PROSE_MODES | {"inspect"}
SAFE_TASKS = PROSE_MODES | {"chapter_analysis", "semantic_review"}
TERMINAL_JOB_STATUSES = {"success", "failed", "canceled", "unknown"}


def _text(value: Any, field: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise WritingAgentContractError(f"invalid {field}")
    return value


def _optional_text(value: Any, field: str, limit: int = 256) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise WritingAgentContractError(f"invalid {field}")
    return value


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise WritingAgentContractError(f"{field} must be boolean")
    return value


def _int(value: Any, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise WritingAgentContractError(f"invalid {field}")
    return value


def _safe_state(raw: Mapping[str, Any]) -> WritingAgentState:
    """Validate and project a state returned by LangGraph or a callback."""
    state: WritingAgentState = {}
    for key in (
        "workflow_id",
        "project_id",
        "branch_id",
        "chapter_id",
        "source_revision_id",
        "source_hash",
        "current_step_id",
        "current_task",
        "current_job_id",
        "current_ai_run_id",
        "current_candidate_hash",
        "analysis_id",
        "adoption_run_id",
        "last_result_ref",
        "error",
    ):
        value = raw.get(key)
        if value is not None:
            state[key] = _optional_text(value, key, 2048 if key in {"error", "last_result_ref"} else 256)
    if raw.get("mode") is not None:
        mode = _text(raw["mode"], "mode", 20)
        if mode not in SAFE_MODES:
            raise WritingAgentContractError("invalid mode")
        state["mode"] = mode
    if raw.get("status") is not None:
        status = _text(raw["status"], "status", 40)
        if status not in SAFE_STATUS:
            raise WritingAgentContractError("invalid status")
        state["status"] = status
    if raw.get("stage") is not None:
        state["stage"] = _text(raw["stage"], "stage", 64)
    for key, low, high in (
        ("step_no", 0, 10),
        ("max_calls", 1, 3),
        ("calls_used", 0, 3),
    ):
        if raw.get(key) is not None:
            state[key] = _int(raw[key], key, low, high)
    if raw.get("authorized") is not None:
        state["authorized"] = _bool(raw["authorized"], "authorized")
    if raw.get("preflight_status") is not None:
        state["preflight_status"] = _text(raw["preflight_status"], "preflight_status", 40)
    return state


@dataclass(frozen=True)
class WritingAgentCallbacks:
    """Trusted service callbacks used by graph nodes.

    Callback return values are intentionally small.  ``enqueue`` must be
    idempotent by ``(workflow_id, step_id)`` and must call the existing
    ``create_ai_job`` path. It must roll back partial job writes before raising.
    ``verify_adoption`` only reads the application receipt after the HTTP layer
    has reused ``apply_candidate`` and returns the adopted revision/hash. The
    ``verify_memory`` callback checks the exact analysis and its saved author
    decisions before review. The graph never writes manuscript or memory rows.
    """

    prepare: Callable[[WritingAgentState], Mapping[str, Any]]
    enqueue: Callable[[WritingAgentState, str, str], Mapping[str, Any]]
    get_job: Callable[[WritingAgentState, str], Mapping[str, Any]]
    verify_adoption: Callable[[WritingAgentState, Mapping[str, Any]], Mapping[str, Any]]
    cancel: Callable[[WritingAgentState], Mapping[str, Any]] | None = None
    verify_memory: Callable[[WritingAgentState], Mapping[str, Any]] | None = None


def _require_callbacks(callbacks: WritingAgentCallbacks) -> None:
    if not isinstance(callbacks, WritingAgentCallbacks):
        raise WritingAgentContractError("callbacks must be WritingAgentCallbacks")


def _job_result(state: WritingAgentState, job: Mapping[str, Any]) -> WritingAgentState:
    status = _text(job.get("status"), "job.status", 32)
    if status not in TERMINAL_JOB_STATUSES | {"queued", "running"}:
        raise WritingAgentContractError("invalid job.status")
    result = job.get("result")
    if result is not None and not isinstance(result, Mapping):
        raise WritingAgentContractError("job.result must be an object")
    job_id = _text(job.get("id") or state.get("current_job_id"), "job.id")
    if job_id != state.get("current_job_id"):
        raise WritingAgentContractError("job identity changed")
    for key in ("project_id", "branch_id", "chapter_id"):
        if job.get(key) is not None and job[key] != state.get(key):
            raise WritingAgentContractError("job scope changed")
    patch: WritingAgentState = {"current_job_id": job_id}
    if job.get("ai_run_id"):
        if state.get("current_ai_run_id") and job["ai_run_id"] != state["current_ai_run_id"]:
            raise WritingAgentContractError("job run identity changed")
        patch["current_ai_run_id"] = _text(job["ai_run_id"], "job.ai_run_id")
    if result and result.get("candidate_hash"):
        patch["current_candidate_hash"] = _text(result["candidate_hash"], "candidate_hash", 128)
    return patch


def _completed_result_issue(job: Mapping[str, Any], task: str) -> str:
    result = job.get("result")
    if not isinstance(result, Mapping) or result.get("safe_to_apply") is not True:
        return "任务结果缺少可采纳的完整证据"
    if result.get("billing_state") not in ("not_dispatched", "usage_reported"):
        return "本次任务费用状态无法确认，流程不会自动继续或重复发送"
    if result.get("completion_state") not in {"complete", "offline_complete"} or result.get("truncated"):
        return "任务结果尚未完整完成，不能继续后续步骤"
    if result.get("restored_read_only") or result.get("materials_read_only"):
        return "任务历史或依赖资料已不能用于后续流程"
    if task in PROSE_MODES:
        validation = result.get("output_validation")
        if isinstance(validation, Mapping) and validation.get("eligible") is False:
            return "正文候选未通过检查，请先整理候选"
    elif task in {"chapter_analysis", "semantic_review"}:
        key = "analysis" if task == "chapter_analysis" else "story_review"
        persisted = result.get(key)
        if not isinstance(persisted, Mapping) or not isinstance(persisted.get("id"), str) or not persisted["id"]:
            return "分析或审稿结果尚未形成可追溯记录，不能继续后续步骤"
    return ""


def _approval_payload(state: WritingAgentState) -> dict[str, Any]:
    return {
        "kind": "writing_agent_authorization",
        "workflow_id": state["workflow_id"],
        "mode": state["mode"],
        "chapter_id": state["chapter_id"],
        "max_calls": state["max_calls"],
        "calls_used": state["calls_used"],
        "message": "确认启动这条有限写作流程；模型调用将按既有每日额度执行，失败或费用未知时停止。",
    }


def _job_wait_payload(state: WritingAgentState, job: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "writing_agent_job",
        "workflow_id": state["workflow_id"],
        "step_id": state["current_step_id"],
        "job_id": state["current_job_id"],
        "ai_run_id": state.get("current_ai_run_id", ""),
        "task": state["current_task"],
        "status": job.get("status"),
        "message": "任务尚未有最终结果；刷新可继续读取同一任务，费用未知时不会自动重试。",
    }


def _adoption_payload(state: WritingAgentState) -> dict[str, Any]:
    return {
        "kind": "writing_agent_adoption",
        "workflow_id": state["workflow_id"],
        "step_id": state["current_step_id"],
        "job_id": state["current_job_id"],
        "ai_run_id": state.get("current_ai_run_id", ""),
        "candidate_hash": state.get("current_candidate_hash", ""),
        "message": "候选已生成。请复用既有候选采纳接口完成采纳，再继续总管流程。",
    }


def _memory_payload(state: WritingAgentState) -> dict[str, Any]:
    return {
        "kind": "writing_agent_memory",
        "workflow_id": state["workflow_id"],
        "chapter_id": state["chapter_id"],
        "analysis_id": state["analysis_id"],
        "message": "请先决定本次章后资料的采用或不采用；全部保存后继续故事审稿。",
    }


def _resume_action(value: Any) -> str:
    if value is True:
        return "approve"
    if not isinstance(value, Mapping):
        return ""
    action = value.get("action")
    return action if isinstance(action, str) else ""


def _is_approved(value: Any) -> bool:
    if value is True:
        return True
    return isinstance(value, Mapping) and value.get("action") in {"approve", "authorize"}


def build_graph(callbacks: WritingAgentCallbacks, checkpointer: Any):
    """Compile the fixed writing graph with a durable LangGraph checkpointer."""
    _require_callbacks(callbacks)
    if StateGraph is None:
        raise WritingAgentDependencyError(
            "LangGraph is required for the writing agent; install the pinned runtime dependency."
        )
    builder = StateGraph(WritingAgentState)

    def prepare(state: Mapping[str, Any]) -> WritingAgentState:
        current = _safe_state(state)
        try:
            prepared = callbacks.prepare(current)
        except Exception as exc:
            return {**current, "status": "requires_action", "stage": "preflight",
                    "error": str(exc)[:2048]}
        if not isinstance(prepared, Mapping):
            raise WritingAgentContractError("prepare must return an object")
        if prepared.get("status", "ready") != "ready":
            return {**current, "status": "requires_action", "stage": "preflight",
                    "preflight_status": "blocked",
                    "error": _optional_text(prepared.get("error"), "preflight.error", 2048)
                    or "当前作品资料尚不能用于这条写作流程"}
        revision = _text(prepared.get("source_revision_id"), "source_revision_id")
        source_hash = _text(prepared.get("source_hash"), "source_hash", 128)
        return {
            **current,
            "status": "awaiting_authorization",
            "stage": "authorization",
            "preflight_status": _optional_text(prepared.get("status"), "preflight_status", 40) or "ready",
            "source_revision_id": revision,
            "source_hash": source_hash,
            "error": "",
        }

    def route_prepare(state: Mapping[str, Any]) -> str:
        return "end" if state.get("status") == "requires_action" else "authorize"

    def authorize(state: Mapping[str, Any]) -> WritingAgentState:
        current = _safe_state(state)
        answer = interrupt(_approval_payload(current))
        if not _is_approved(answer):
            return {**current, "status": "canceled", "stage": "authorization",
                    "error": "作者未授权本次有限流程"}
        return {**current, "authorized": True, "status": "running",
                "stage": "enqueue_continue" if current["mode"] in PROSE_MODES else "enqueue_analysis",
                "step_no": 0}

    def route_authorize(state: Mapping[str, Any]) -> str:
        if state.get("status") == "canceled":
            return "end"
        return "enqueue_continue" if state.get("mode") in PROSE_MODES else "enqueue_analysis"

    def enqueue_task(state: Mapping[str, Any], task: str, stage: str, step_no: int) -> WritingAgentState:
        current = _safe_state(state)
        if current.get("authorized") is not True:
            return {**current, "status": "requires_action", "stage": stage,
                    "error": "本次有限流程尚未得到授权"}
        if current.get("calls_used", 0) >= current.get("max_calls", 1):
            return {**current, "status": "requires_action", "stage": stage,
                    "error": "已达到本次流程模型调用上限"}
        step_id = f"{current['workflow_id']}:{step_no}:{task}"
        try:
            queued = callbacks.enqueue(current, task, step_id)
        except Exception as exc:
            return {**current, "status": "requires_action", "stage": stage,
                    "error": str(exc)[:2048]}
        if not isinstance(queued, Mapping):
            raise WritingAgentContractError("enqueue must return an object")
        job_id = _text(queued.get("id") or queued.get("job_id"), "job_id")
        run_id = _optional_text(queued.get("ai_run_id"), "ai_run_id")
        return {
            **current,
            "status": "awaiting_job",
            "stage": stage,
            "step_no": step_no,
            "current_step_id": step_id,
            "current_task": task,
            "current_job_id": job_id,
            "current_ai_run_id": run_id,
            "current_candidate_hash": "",
            "calls_used": current.get("calls_used", 0) + 1,
            "error": "",
        }

    def enqueue_continue(state: Mapping[str, Any]) -> WritingAgentState:
        task = state["mode"]
        return enqueue_task(state, task, f"{task}_generation", 1)

    def route_enqueue_continue(state: Mapping[str, Any]) -> str:
        return "end" if state.get("status") == "requires_action" else "wait_continue"

    def enqueue_analysis(state: Mapping[str, Any]) -> WritingAgentState:
        return enqueue_task(state, "chapter_analysis", "chapter_analysis_generation", 2 if state.get("mode") in PROSE_MODES else 1)

    def route_enqueue_analysis(state: Mapping[str, Any]) -> str:
        return "end" if state.get("status") == "requires_action" else "wait_analysis"

    def enqueue_review(state: Mapping[str, Any]) -> WritingAgentState:
        return enqueue_task(state, "semantic_review", "semantic_review_generation", 3 if state.get("mode") in PROSE_MODES else 2)

    def route_enqueue_review(state: Mapping[str, Any]) -> str:
        return "end" if state.get("status") == "requires_action" else "wait_review"

    def wait_task(state: Mapping[str, Any], next_stage: str, task: str) -> WritingAgentState:
        current = _safe_state(state)
        try:
            job = callbacks.get_job(current, current["current_job_id"])
        except Exception as exc:
            return {**current, "status": "requires_action", "stage": current.get("stage", ""),
                    "error": str(exc)[:2048]}
        if not isinstance(job, Mapping):
            raise WritingAgentContractError("get_job must return an object")
        status = _text(job.get("status"), "job.status", 32)
        try:
            patch = _job_result(current, job)
        except WritingAgentContractError as exc:
            return {**current, "status": "requires_action", "stage": current.get("stage", ""),
                    "error": str(exc)[:2048]}
        if status in {"queued", "running"}:
            answer = interrupt(_job_wait_payload({**current, **patch}, job))
            if _resume_action(answer) == "cancel":
                return {**current, **patch, "status": "canceled", "error": "作者停止了当前任务"}
            # Re-entering this node after ``advance`` reads the same job again.
            return {**current, **patch}
        if status != "success":
            return {**current, **patch, "status": "requires_action",
                    "stage": current.get("stage", ""), "error": _optional_text(
                        job.get("error"), "job.error", 2048
                    ) or f"{task}任务未成功完成（{status}）"}
        issue = _completed_result_issue(job, task)
        if issue:
            return {**current, **patch, "status": "requires_action",
                    "stage": current.get("stage", ""),
                    "error": issue}
        if task == "chapter_analysis":
            patch["analysis_id"] = _text(job["result"]["analysis"]["id"], "analysis_id")
        return {**current, **patch,
                "status": ("awaiting_adoption" if task in PROSE_MODES else
                           "awaiting_memory" if task == "chapter_analysis" else "running"),
                "stage": next_stage, "error": ""}

    def wait_continue(state: Mapping[str, Any]) -> WritingAgentState:
        return wait_task(state, "candidate_adoption", state["mode"])

    def route_wait_continue(state: Mapping[str, Any]) -> str:
        if state.get("status") == "requires_action":
            return "end"
        if state.get("status") == "canceled":
            return "end"
        if state.get("status") == "awaiting_job":
            return "wait_continue"
        return "adopt"

    def adopt_continue(state: Mapping[str, Any]) -> WritingAgentState:
        current = _safe_state(state)
        answer = interrupt(_adoption_payload(current))
        action = _resume_action(answer)
        if action == "cancel":
            return {**current, "status": "canceled", "stage": "candidate_adoption",
                    "error": "作者停止采纳"}
        if action not in {"adopt", "applied", "candidate_applied"}:
            return {**current, "status": "requires_action", "stage": "candidate_adoption",
                    "error": "候选尚未通过既有采纳接口确认"}
        try:
            verified = callbacks.verify_adoption(current, answer if isinstance(answer, Mapping) else {})
        except Exception as exc:
            return {**current, "status": "requires_action", "stage": "candidate_adoption",
                    "error": str(exc)[:2048]}
        if not isinstance(verified, Mapping) or verified.get("applied") is not True:
            return {**current, "status": "requires_action", "stage": "candidate_adoption",
                    "error": "既有采纳接口尚未确认本次候选"}
        if verified.get("ai_run_id") != current.get("current_ai_run_id"):
            return {**current, "status": "requires_action", "stage": "candidate_adoption",
                    "error": "采纳回执未对应本次正文候选"}
        try:
            adopted_revision = _text(verified.get("source_revision_id"), "adopted source_revision_id")
            adopted_hash = _text(verified.get("source_hash"), "adopted source_hash", 128)
        except WritingAgentContractError as exc:
            return {**current, "status": "requires_action", "stage": "candidate_adoption",
                    "error": str(exc)[:2048]}
        if adopted_revision == current.get("source_revision_id") or adopted_hash == current.get("source_hash"):
            return {**current, "status": "requires_action", "stage": "candidate_adoption",
                    "error": "采纳后尚未形成新的正文版本，不能对旧稿继续分析"}
        return {
            **current,
            "status": "running",
            "stage": "enqueue_analysis",
            "adoption_run_id": _optional_text(verified.get("ai_run_id") or current.get("current_ai_run_id"), "adoption_run_id"),
            "source_revision_id": adopted_revision,
            "source_hash": adopted_hash,
            "last_result_ref": _optional_text(verified.get("result_ref"), "result_ref", 2048),
            "error": "",
        }

    def route_adopt(state: Mapping[str, Any]) -> str:
        return "end" if state.get("status") in {"requires_action", "canceled"} else "enqueue_analysis"

    def wait_analysis(state: Mapping[str, Any]) -> WritingAgentState:
        return wait_task(state, "memory_confirmation", "chapter_analysis")

    def route_wait_analysis(state: Mapping[str, Any]) -> str:
        if state.get("status") in {"requires_action", "canceled"}:
            return "end"
        if state.get("status") == "awaiting_job":
            return "wait_analysis"
        return "wait_memory"

    def wait_memory(state: Mapping[str, Any]) -> WritingAgentState:
        current = _safe_state(state)
        answer = interrupt(_memory_payload(current))
        if _resume_action(answer) == "cancel":
            return {**current, "status": "canceled", "error": "作者停止了章后确认"}
        if _resume_action(answer) != "advance":
            return {**current, "status": "requires_action",
                    "error": "请先保存本次章后资料的决定"}
        try:
            if callbacks.verify_memory is None:
                raise WritingAgentContractError("缺少章后资料确认校验，未启动审稿")
            verified = callbacks.verify_memory(current)
            if (not isinstance(verified, Mapping) or
                    verified.get("analysis_id") != current["analysis_id"] or
                    type(verified.get("ready")) is not bool):
                raise WritingAgentContractError("章后资料确认回执与本次分析不一致")
        except Exception as exc:
            return {**current, "status": "requires_action", "error": str(exc)[:2048]}
        if not verified["ready"]:
            return {**current, "status": "awaiting_memory", "error": ""}
        return {**current, "status": "running", "stage": "enqueue_review", "error": ""}

    def route_wait_memory(state: Mapping[str, Any]) -> str:
        if state.get("status") in {"requires_action", "canceled"}:
            return "end"
        return "wait_memory" if state.get("status") == "awaiting_memory" else "enqueue_review"

    def wait_review(state: Mapping[str, Any]) -> WritingAgentState:
        return wait_task(state, "review_ready", "semantic_review")

    def route_wait_review(state: Mapping[str, Any]) -> str:
        if state.get("status") in {"requires_action", "canceled"}:
            return "end"
        if state.get("status") == "awaiting_job":
            return "wait_review"
        return "finish"

    def finish(state: Mapping[str, Any]) -> WritingAgentState:
        current = _safe_state(state)
        return {**current, "status": "review_ready", "stage": "review_ready", "error": ""}

    builder.add_node("prepare", prepare)
    builder.add_node("authorize", authorize)
    builder.add_node("enqueue_continue", enqueue_continue)
    builder.add_node("wait_continue", wait_continue)
    builder.add_node("adopt_continue", adopt_continue)
    builder.add_node("enqueue_analysis", enqueue_analysis)
    builder.add_node("wait_analysis", wait_analysis)
    builder.add_node("wait_memory", wait_memory)
    builder.add_node("enqueue_review", enqueue_review)
    builder.add_node("wait_review", wait_review)
    builder.add_node("finish", finish)
    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", route_prepare, {"authorize": "authorize", "end": END})
    builder.add_conditional_edges("authorize", route_authorize, {
        "enqueue_continue": "enqueue_continue",
        "enqueue_analysis": "enqueue_analysis",
        "end": END,
    })
    builder.add_conditional_edges("enqueue_continue", route_enqueue_continue, {"wait_continue": "wait_continue", "end": END})
    builder.add_conditional_edges("wait_continue", route_wait_continue, {
        "adopt": "adopt_continue", "wait_continue": "wait_continue", "end": END,
    })
    builder.add_conditional_edges("adopt_continue", route_adopt, {"enqueue_analysis": "enqueue_analysis", "end": END})
    builder.add_conditional_edges("enqueue_analysis", route_enqueue_analysis, {"wait_analysis": "wait_analysis", "end": END})
    builder.add_conditional_edges("wait_analysis", route_wait_analysis, {
        "wait_memory": "wait_memory", "wait_analysis": "wait_analysis", "end": END,
    })
    builder.add_conditional_edges("wait_memory", route_wait_memory, {
        "enqueue_review": "enqueue_review", "wait_memory": "wait_memory", "end": END,
    })
    builder.add_conditional_edges("enqueue_review", route_enqueue_review, {"wait_review": "wait_review", "end": END})
    builder.add_conditional_edges("wait_review", route_wait_review, {
        "finish": "finish", "wait_review": "wait_review", "end": END,
    })
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)


def graph_config(workflow_id: str, *, thread_id: str | None = None) -> dict[str, Any]:
    """Return the stable LangGraph thread configuration for one workflow."""
    _text(workflow_id, "workflow_id", 256)
    return {"configurable": {"thread_id": _text(thread_id or workflow_id, "thread_id", 512)}}


def initial_state(
    workflow_id: str,
    project_id: str,
    branch_id: str,
    chapter_id: str,
    mode: str,
    *,
    max_calls: int,
) -> WritingAgentState:
    """Build the only state accepted for a new graph thread."""
    for value, field in (
        (workflow_id, "workflow_id"),
        (project_id, "project_id"),
        (branch_id, "branch_id"),
        (chapter_id, "chapter_id"),
    ):
        _text(value, field)
    result = _safe_state({
        "workflow_id": workflow_id,
        "project_id": project_id,
        "branch_id": branch_id,
        "chapter_id": chapter_id,
        "mode": mode,
        "status": "planned",
        "stage": "preflight",
        "step_no": 0,
        "max_calls": max_calls,
        "calls_used": 0,
        "authorized": False,
    })
    if mode not in SAFE_MODES:
        raise WritingAgentContractError("invalid mode")
    if max_calls != (3 if mode in PROSE_MODES else 2):
        raise WritingAgentContractError("max_calls must match the hard limit for this mode")
    return result


def invoke_start(graph: Any, state: WritingAgentState, *, thread_id: str | None = None) -> Mapping[str, Any]:
    """Start a workflow; it normally returns an authorization interrupt."""
    return graph.invoke(
        dict(_safe_state(state)), graph_config(state["workflow_id"], thread_id=thread_id),
        durability="sync",
    )


def invoke_resume(graph: Any, workflow_id: str, value: Any, *, thread_id: str | None = None) -> Mapping[str, Any]:
    """Resume one checkpoint with a JSON-serializable action."""
    if Command is None:
        raise WritingAgentDependencyError("LangGraph is required for resume")
    return graph.invoke(
        Command(resume=value), graph_config(workflow_id, thread_id=thread_id),
        durability="sync",
    )


__all__ = [
    "WritingAgentCallbacks",
    "WritingAgentDependencyError",
    "WritingAgentContractError",
    "WritingAgentState",
    "build_graph",
    "graph_config",
    "initial_state",
    "invoke_resume",
    "invoke_start",
]
