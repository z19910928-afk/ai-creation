"""Run two unmodified source components with dependency stubs; no network or DB."""
import ast
import asyncio
import json
import re
import typing
from pathlib import Path

ROOT = Path("./.audit-cache/2026-10-13/references/NovelForge")


class QuietLogger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def load_class(relative_path, class_name, namespace):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text())
    node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    if class_name == "ForeshadowService":
        node.body = [node for node in node.body if getattr(node, "name", "") == "suggest"]
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[class_name]


async def scheduler_probe():
    scheduler_cls = load_class(
        "backend/app/services/workflow/engine/scheduler.py",
        "WorkflowScheduler",
        {"asyncio": asyncio, "Dict": typing.Dict, "Optional": typing.Optional, "logger": QuietLogger()},
    )
    scheduler = scheduler_cls(max_concurrent_runs=1)
    gate = asyncio.Event()
    order = []

    async def work(run_id):
        if run_id == 1:
            await gate.wait()
        order.append(run_id)

    await scheduler.schedule_run(1, work(1), priority=0)
    await scheduler.schedule_run(2, work(2), priority=100)
    await scheduler.schedule_run(3, work(3), priority=-100)
    pending_cancel = scheduler.cancel_run(3)
    gate.set()
    for _ in range(100):
        await asyncio.sleep(0)
        if not scheduler.running_runs and scheduler.pending_queue.empty():
            break
    assert order == [1, 2, 3], order
    assert pending_cancel is False
    return {
        "order": order,
        "submitted_priorities": {"2": 100, "3": -100},
        "pending_cancel_result": pending_cancel,
        "scope": "Original scheduler class AST; logger stub; in-process asyncio only. Current reachable workflow paths use WorkflowRuntime instead.",
    }


def suggest_probe():
    service_cls = load_class(
        "backend/app/services/foreshadow_service.py",
        "ForeshadowService",
        {"re": re, "Dict": typing.Dict, "Any": typing.Any, "List": typing.List},
    )
    service = service_cls()
    result = service.suggest("林岚准备回到旧城，必须找回铜镜。")
    assert result["goals"]
    assert result["items"]
    return {
        "output": result,
        "scope": "Original suggest method AST; no SQLModel import, database, UI, or model call.",
    }


async def main():
    print(json.dumps({"scheduler": await scheduler_probe(), "foreshadow_suggest": suggest_probe()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
