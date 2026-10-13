"""Scoped, read-only project queries and explicit candidate preparation."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time


_SIGNING_KEY = os.urandom(32)
TOOLS = {
    "continue": {"label": "续写当前章", "target": "正文末尾的待采纳候选"},
    "polish": {"label": "润色选中文字", "target": "所选原句的待采纳候选"},
    "summary": {"label": "整理章节回顾", "target": "回顾与下一章任务候选"},
    "chapter_analysis": {"label": "提取章后记忆", "target": "带原句的待确认记忆"},
    "semantic_review": {"label": "故事审稿", "target": "有证据的审稿建议"},
    "outline": {"label": "准备故事总纲", "target": "总纲候选"},
    "scene": {"label": "准备本章场景", "target": "场景计划候选"},
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def fingerprint(state, structure):
    material = {
        "project": state["project"], "version": state.get("version"),
        "head_revision": state.get("head_revision"), "chapters": state["chapters"],
        "records": state["records"], "memory": state["memory"],
        "plot": state["plot"], "structure": structure,
    }
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


def intent(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("请填写 1 到 2000 个字的问题或创作要求。")
    for task, words in (
        ("polish", ("润色", "改写", "修改选中")),
        ("chapter_analysis", ("记忆", "提取事实", "章后")),
        ("semantic_review", ("审稿", "矛盾", "连续性", "逻辑检查")),
        ("summary", ("总结", "回顾", "概括", "交接")),
        ("outline", ("大纲", "总纲", "规划全书")),
        ("scene", ("场景", "规划本章")),
        ("continue", ("续写", "接着写", "往下写")),
    ):
        if any(word in query for word in words):
            return task
    return None


def preview(state, structure, scope, query, chapter, source, results):
    task = intent(query)
    answer = {
        "query": query.strip(), "read_only": True, "results": results,
        "scope": {"project_id": scope["project_id"], "branch_id": scope["branch_id"]},
        "answer": f"找到 {len(results)} 条当前作品资料。" if results else "当前作品中没有找到匹配资料。",
        "proposal": None,
    }
    if task:
        if not chapter or not source:
            raise ValueError("请先选中本作品的章节。")
        stamp = int(time.time())
        frozen_source = {key: source[key] for key in (
            "project_id", "branch_id", "chapter_id", "source_revision_id", "source_body_hash", "mode"
        )}
        frozen_source["range"] = {key: source["range"][key] for key in ("start", "end")}
        record = {
            "project_id": scope["project_id"], "branch_id": scope["branch_id"],
            "chapter_id": chapter["id"], "task": task, "instruction": query.strip(),
            "source": frozen_source, "fingerprint": fingerprint(state, structure),
            "created_at": stamp, "expires_at": stamp + 1800,
        }
        raw = _json(record).encode("utf-8")
        token = base64.urlsafe_b64encode(raw).decode("ascii") + "." + hmac.new(_SIGNING_KEY, raw, hashlib.sha256).hexdigest()
        answer["proposal"] = {
            "token": token, "task": task, **TOOLS[task],
            "chapter_id": chapter["id"], "chapter_title": chapter["title"],
            "source": frozen_source, "instruction": query.strip(),
        }
        answer["answer"] = f"已准备《{chapter['title']}》的{TOOLS[task]['label']}要求，等待确认生成。"
    return answer


def confirm(token, state, structure, scope):
    if not isinstance(token, str) or len(token) > 200_000:
        raise ValueError("助手预览无效，请重新查看。")
    try:
        encoded, signature = token.rsplit(".", 1)
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
        expected = hmac.new(_SIGNING_KEY, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        proposal = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("助手预览校验失败，请重新查看。") from None
    if (proposal.get("project_id") != scope["project_id"] or
            proposal.get("branch_id") != scope["branch_id"] or
            proposal.get("task") not in TOOLS):
        raise ValueError("这份助手预览不属于当前作品。")
    if proposal.get("expires_at", 0) < time.time():
        raise ValueError("助手预览已过期，请重新查看。")
    if proposal.get("fingerprint") != fingerprint(state, structure):
        raise ValueError("预览之后稿件或资料已经变化，请重新查看再确认。")
    return proposal
