"""Exact manuscript payloads embedded in readable publication exports."""
from __future__ import annotations

import base64
import hashlib
import json
import re


SOURCE_PATH = "META-INF/moyu-manuscript.json"
MAX_SOURCE_BYTES = 16 * 1024 * 1024


def source_bytes(state, chapters):
    payload = {
        "format": "moyu-manuscript-v1", "version": 1,
        "project": {key: state["project"].get(key) for key in ("title", "genre", "summary")},
        "chapters": [{key: chapter.get(key, "") for key in ("title", "body")} for chapter in chapters],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("出版正文超过当前 16 MB 支持范围，请分卷导出。")
    return raw


def decode_source(raw, expected_hash):
    if len(raw) > MAX_SOURCE_BYTES or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("出版包的正文校验失败。")
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or payload.get("format") != "moyu-manuscript-v1" or
            type(payload.get("version")) is not int or payload["version"] != 1):
        raise ValueError("出版包的正文格式不受支持。")
    project = payload.get("project")
    if not isinstance(project, dict) or any(
        value is not None and not isinstance(value, str) for value in project.values()
    ):
        raise ValueError("出版包的作品资料无效。")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= 2000:
        raise ValueError("出版包的章节列表无效。")
    for chapter in chapters:
        if (not isinstance(chapter, dict) or not isinstance(chapter.get("title"), str) or
                not chapter["title"].strip() or len(chapter["title"]) > 500 or
                not isinstance(chapter.get("body"), str) or len(chapter["body"]) > 2_000_000):
            raise ValueError("出版包的章节内容无效。")
    return payload


def markdown(state, chapters, manifest, raw):
    project = state["project"]
    yaml = lambda value: json.dumps(str(value or ""), ensure_ascii=False)
    lines = [
        "---", "format: moyu-markdown-v2",
        "title: " + yaml(project.get("title")), "genre: " + yaml(project.get("genre")),
        "summary: " + yaml(project.get("summary")), "chapter_count: " + str(len(chapters)),
        "source_sha256: " + yaml(manifest["source_sha256"]),
        "loss_manifest: " + yaml(json.dumps(manifest["loss_manifest"], ensure_ascii=False)),
        "---", "", "# " + re.sub(r"[\r\n]+", " ", project.get("title") or "未命名作品"), "",
    ]
    for index, chapter in enumerate(chapters, 1):
        title = re.sub(r"[\r\n]+", " ", chapter["title"])
        lines.extend([f"## {index}. {title}", "", chapter["body"], ""])
    lines.extend([
        "<!-- moyu-manuscript-v1 " + hashlib.sha256(raw).hexdigest() + " " +
        base64.b64encode(raw).decode("ascii") + " -->", "",
    ])
    return "\n".join(lines).encode("utf-8")


def markdown_source(text):
    matches = list(re.finditer(r"<!-- moyu-manuscript-v1 ([0-9a-f]{64}) ([A-Za-z0-9+/=]+) -->", text))
    if not matches:
        if "<!-- moyu-manuscript-v1" in text:
            raise ValueError("出版文件的正文清单已损坏。")
        return None
    if len(matches) != 1:
        raise ValueError("出版文件包含重复正文清单。")
    marker = matches[0]
    return decode_source(base64.b64decode(marker.group(2), validate=True), marker.group(1))
