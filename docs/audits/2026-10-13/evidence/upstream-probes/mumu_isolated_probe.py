"""Offline probes of unchanged MuMu source in deliberately narrow harnesses."""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, HTTPException


ROOT = Path("./.audit-cache/2026-10-13/references/MuMuAINovel")
logger = logging.getLogger("mumu-audit")


def load_definition(relative_path, name, extra=None):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text())
    definition = next(node for node in tree.body if getattr(node, "name", None) == name)
    definition.decorator_list = []
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), definition],
        type_ignores=[],
    )
    namespace = {"logger": logger, "re": re, "Depends": Depends, "HTTPException": HTTPException}
    namespace.update(extra or {})
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def parser_probe():
    parser_class = load_definition("backend/app/services/txt_parser_service.py", "TxtParserService")
    parser = parser_class()
    original = "第一章 起点\n甲打开了门。\n\n第二章 相遇\n乙拿着钥匙。"
    text, encoding = parser.decode_bytes(original.encode("gb18030"))
    chapters = parser.split_chapters(parser.clean_text(text))
    assert encoding == "gb18030"
    assert [item["title"] for item in chapters] == ["第一章 起点", "第二章 相遇"]
    assert [item["chapter_number"] for item in chapters] == [1, 2]
    short_preface = "这是一段应该保留的序言。\n第一章 起点\n甲打开了门。"
    parsed_preface = parser.split_chapters(short_preface)
    preface_is_preserved = any("这是一段应该保留的序言。" in item["content"] for item in parsed_preface)
    assert preface_is_preserved is False
    return {
        "scope": "unchanged TxtParserService with only logger dependency replaced",
        "gb18030_two_chapters": "pass",
        "short_preface_preserved": preface_is_preserved,
        "short_preface_original_chars": len(short_preface),
        "short_preface_parsed": parsed_preface,
    }


def temporal_state_probe():
    append = load_definition(
        "backend/app/services/chapter_context_service.py",
        "_append_current_state_lines",
        {"STATUS_LABELS": {"deceased": "死亡"}},
    )
    lines = []
    append(
        lines,
        SimpleNamespace(
            status="deceased",
            status_changed_chapter=50,
            current_state="第五十章才揭示的秘密",
            state_updated_chapter=50,
        ),
    )
    assert any("第五十章才揭示的秘密" in line for line in lines)
    return {
        "scope": "unchanged helper only; no full chapter generation",
        "latest_state_appended_without_chapter_filter": True,
        "output": lines,
    }


class Field:
    def __eq__(self, value):
        return value


class FakeChapter:
    id = Field()


class FakeProject:
    id = Field()


class Query:
    def __init__(self, model):
        self.model = model

    def where(self, _):
        return self


class FakeDB:
    def __init__(self, chapter, project):
        self.chapter = chapter
        self.project = project
        self.commits = 0

    async def execute(self, query):
        item = self.chapter if query.model is FakeChapter else self.project
        return SimpleNamespace(scalar_one_or_none=lambda: item)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _):
        return None


async def apply_probe():
    async def verify_access(*_):
        return None

    apply = load_definition(
        "backend/app/api/chapters.py",
        "apply_partial_regenerate",
        {
            "get_db": lambda: None,
            "verify_project_access": verify_access,
            "select": Query,
            "Chapter": FakeChapter,
            "Project": FakeProject,
        },
    )
    selected_at_generation = "甲推门"
    current_body = "甲关门。乙正在等待。"
    chapter = SimpleNamespace(
        id="chapter1", project_id="project1", content=current_body, word_count=len(current_body)
    )
    project = SimpleNamespace(current_words=len(current_body))
    database = FakeDB(chapter, project)
    result = await apply(
        "chapter1",
        SimpleNamespace(state=SimpleNamespace(user_id="offline-audit")),
        {"new_text": "甲猛地推门", "start_position": 0, "end_position": 3},
        database,
    )
    assert result["success"] is True
    assert chapter.content == "甲猛地推门。乙正在等待。"
    assert database.commits == 1
    return {
        "scope": "unchanged apply endpoint function with fake permission/query/DB only; not HTTP integration",
        "source_selected_at_generation": selected_at_generation,
        "source_at_apply": current_body,
        "accepted_without_source_hash_or_revision": True,
        "persisted_body": chapter.content,
        "result": result,
    }


def registration_probe():
    main_tree = ast.parse((ROOT / "backend/app/main.py").read_text())
    registered = []
    for node in ast.walk(main_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router":
            if node.args and isinstance(node.args[0], ast.Attribute) and isinstance(node.args[0].value, ast.Name):
                registered.append(node.args[0].value.id)
    assert "chapters" in registered
    assert "polish" not in registered
    return {"scope": "AST registry inspection only", "polish_router_registered": False}


async def main():
    payload = {
        "source_sha": "99f0aec6ce3b1c65652d20007be930ebba6a6d20",
        "network_requests": 0,
        "real_ai_requests": 0,
        "parser": parser_probe(),
        "temporal_state": temporal_state_probe(),
        "partial_apply": await apply_probe(),
        "router_registration": registration_probe(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
