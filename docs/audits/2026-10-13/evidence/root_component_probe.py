#!/usr/bin/env python3
"""Reproduce audit findings without external models or a production database."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def context_probe(server):
    evidence = "月牙密钥藏在左靴内侧"
    old_body = "旧章内容。" * 240 + evidence + "其他旧章内容。" * 450
    future_quote = "林岚的父亲是最终凶手"
    state = {
        "project": {"title": "隔离审查样本", "summary": "寻找月牙密钥", "goal": 5000},
        "chapters": [
            {"id": "past", "title": "第一章", "body": old_body, "status": "完成"},
            {
                "id": "now", "title": "第二章", "body": "林岚寻找月牙密钥。",
                "status": "进行中", "pov": "林岚", "chapter_goal": "找到密钥",
            },
            {"id": "future", "title": "第三章", "body": future_quote, "status": "完成"},
        ],
        "records": [],
        "plot": {"setup": [], "develop": [], "payoff": []},
        "memory": [{
            "id": "future_memory", "type": "人物", "name": "林岚父亲",
            "status": "accepted", "source_chapter_id": "future",
            "source_span_id": "span_future", "source_span_status": "valid",
            "source_quote": future_quote, "source_start": 0,
            "source_end": len(future_quote),
        }],
    }
    structure = {
        "relationships": [], "foreshadowing": [], "conflicts": [], "plot_threads": [],
        "timeline": [{
            "id": "future_timeline", "chapter_id": "future",
            "title": future_quote, "status": "occurred",
        }],
        "character_states": [{
            "id": "villain_secret", "valid_start": "chapter-1",
            "knowledge_scope": {"entities": ["villain"]},
            "attribute": "真凶身份", "value": {"value": future_quote},
        }],
    }
    with patch.object(server, "state_from_db", return_value=copy.deepcopy(state)), \
            patch.object(server, "story_map", return_value=copy.deepcopy(structure)):
        context, _ = server.ai_context(None, "now")
    return {
        "old_chapter_selected": any(c["id"] == "past" for c in context["retrieved_chapters"]),
        "old_chapter_middle_evidence_in_prompt": any(
            evidence in c["excerpt"] for c in context["retrieved_chapters"]
        ),
        "future_memory_ids": [m["id"] for m in context["confirmed_memory"]],
        "future_timeline_ids": [m["id"] for m in context["story_structure"]["timeline"]],
        "villain_only_state_ids_in_hero_pov": [
            m["id"] for m in context["story_structure"]["character_states"]
        ],
        "memory_limit_zero_count": len(server.bounded_context_memory(state["memory"], 0)),
    }


def polish_probe(server):
    marker = "不可删除的章首哨兵"
    body = marker + "完整章节内容。" * 2200 + "章尾。"
    context = {"project": {}, "current_chapter": {"id": "long", "body_tail": body}}
    server.fit_context_budget(context, 64000)
    actual = context["current_chapter"]["body_tail"]
    return {
        "original_characters": len(body),
        "sent_current_body_characters": len(actual),
        "chapter_head_sentinel_present": marker in actual,
        "scope": "Actual fit_context_budget component; browser overwrite tested separately",
    }


def review_probe(runtime):
    with runtime.app() as (base, _, _):
        _, state = runtime.request(base, "GET", "/api/state")
        chapter = state["chapters"][0]
        chapter["body"] = "林岚沿着旧码头走到灯塔。她把钥匙交给守门人，随后返回街市。" * 5
        chapter["status"] = "完成"
        state["project"]["title"] = "独立审查样本"
        state["project"]["summary"] = "追查码头旧案。"
        state["memory"] = []
        saved, _ = runtime.request(base, "PUT", "/api/state", state)
        reviewed, report = runtime.request(
            base, "POST", "/api/review", {"chapter_id": chapter["id"]}
        )
        new_body = "另一份稿件：苍遥乘飞船前往火星，所有旧人物和事件均未出现。" * 6
        edited, result = runtime.request(
            base, "PATCH", "/api/chapters/" + chapter["id"],
            {"body": new_body, "status": "完成"},
        )
        exported, epub = runtime.request(base, "GET", "/api/export/manifest")
        return {
            "initial_save_http": saved, "review_http": reviewed,
            "review_status_before_edit": report["status"], "edit_http": edited,
            "review_status_after_edit": result.get("review_status"),
            "export_manifest_http": exported,
            "finalization_after_changed_body": epub.get("finalization"),
            "scope": "Real local HTTP, temporary SQLite database, offline mode",
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("root-components.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    server = load_module("audit_root_server", root / "server.py")
    runtime = load_module("audit_runtime_helper", Path(__file__).with_name("runtime_probe.py"))
    runtime.ROOT = root
    results = {
        "context_scope_and_middle_evidence": context_probe(server),
        "long_chapter_polish_scope": polish_probe(server),
        "review_not_bound_to_body_revision": review_probe(runtime),
    }
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
