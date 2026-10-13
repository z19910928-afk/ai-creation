"""Execute selected upstream components without network or database access."""
import ast
import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence


ROOT = Path("./.audit-cache/2026-10-13/references/arboris-novel")


def extract(relative_path, names, namespace):
    source = ast.parse((ROOT / relative_path).read_text())
    nodes = [
        node
        for node in source.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in names
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(ROOT / relative_path), "exec"), namespace)
    return namespace


async def main():
    base = {
        "Optional": Optional,
        "List": List,
        "Dict": Dict,
        "LLMService": object,
        "PromptService": object,
        "logger": logging.getLogger("arboris-probe"),
    }
    extract(
        "backend/app/api/routers/writer.py",
        {"_rewrite_with_guardrails"},
        base,
    )

    class Prompts:
        async def get_prompt(self, name):
            return "configured rewrite prompt"

    repair_result = await base["_rewrite_with_guardrails"](
        object(), Prompts(), "original text", {}, "violation", 1
    )

    ingestion = {
        "Optional": Optional,
        "List": List,
        "Dict": Dict,
        "Sequence": Sequence,
        "LLMService": object,
        "VectorStoreService": object,
    }
    extract(
        "backend/app/services/chapter_ingest_service.py",
        {"ChapterIngestionService"},
        ingestion,
    )
    missing_argument = None
    try:
        inspect.signature(ingestion["ChapterIngestionService"].ingest_chapter).bind(
            object(),
            project_id="book",
            chapter_number=1,
            title="chapter one",
            content="accepted chapter",
            summary=None,
        )
    except TypeError as exc:
        missing_argument = str(exc)

    visibility = {"Optional": Optional, "List": List, "Dict": Dict, "Set": set}
    import re
    from copy import deepcopy

    visibility.update(re=re, deepcopy=deepcopy)
    extract(
        "backend/app/services/writer_context_builder.py",
        {"_detect_names", "_shallow_copy_blueprint", "WriterContextBuilder"},
        visibility,
    )
    blueprint = {
        "characters": [
            {"name": "hero", "identity": "knows nothing"},
            {"name": "villain", "secret": "stole the diamond"},
        ],
        "world_setting": {"future_truth": "villain stole the diamond"},
        "full_synopsis": "ending reveal",
        "relationships": [{"from": "hero", "to": "villain", "description": "secret"}],
    }
    filtered = visibility["WriterContextBuilder"]().build_visibility_context(
        blueprint,
        ["hero meets villain"],
        "",
        "hero chapter",
        "hero searches without knowing the culprit",
        "",
    )

    @dataclass
    class Chunk:
        content: str
        chapter_number: int
        chapter_title: str = ""

    class LLM:
        async def get_embedding(self, query, user_id):
            return [1.0]

    class Store:
        async def query_chunks(self, **kwargs):
            return [Chunk("future ending secret", 9)]

        async def query_summaries(self, **kwargs):
            return []

    context = {
        "dataclass": dataclass,
        "List": List,
        "Optional": Optional,
        "LLMService": LLM,
        "VectorStoreService": Store,
        "RetrievedChunk": Chunk,
        "RetrievedSummary": object,
        "settings": SimpleNamespace(vector_store_enabled=True),
        "logger": logging.getLogger("arboris-probe"),
    }
    extract(
        "backend/app/services/chapter_context_service.py",
        {"ChapterRAGContext", "ChapterContextService"},
        context,
    )
    rag = await context["ChapterContextService"](
        llm_service=LLM(), vector_store=Store()
    ).retrieve_for_generation(project_id="book", query_text="chapter 2", user_id=1)

    tree = ast.parse((ROOT / "backend/app/services/novel_service.py").read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "NovelService")
    methods = {node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    result = {
        "scope": "AST-isolated original components; fake prompt, embedding and vector store; no HTTP, database or paid model",
        "guardrail_configured_returns_none": repair_result is None,
        "select_ingest_signature_error": missing_argument,
        "visibility_keeps_villain_secret": any(
            item.get("secret") == "stole the diamond"
            for item in filtered["writer_blueprint"]["characters"]
        ),
        "visibility_keeps_world_future_truth": "future_truth" in filtered["writer_blueprint"]["world_setting"],
        "simple_rag_keeps_future_chapter": [item.chapter_number for item in rag.chunks],
        "called_delete_chapter_exists": "delete_chapter" in methods,
        "defined_delete_chapters_exists": "delete_chapters" in methods,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
