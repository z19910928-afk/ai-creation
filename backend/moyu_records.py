"""Persist author-controlled knowledge boundaries without changing old cards."""
from __future__ import annotations

import copy
import json

from .moyu_context import EPISTEMIC_MODES, chapter_number


FIELDS = {"knowledge_scope", "known_by", "epistemic_mode", "available_from",
          "valid_start", "valid_end", "private", "branch_id"}
LIST_FIELDS = {"known_by", "characters", "entities", "branches", "branch_ids", "chapters"}
SCOPE_FIELDS = LIST_FIELDS | {
    "project_id", "branch_id", "available_from", "valid_start", "valid_end",
    "story_time", "time_from", "time_to", "epistemic_mode", "subject_entity_id", "private",
}


def install_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS app_entity_scope(
        entity_id TEXT PRIMARY KEY REFERENCES entity(id),
        scope_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL)""")


def read_scope(conn, entity_id):
    row = conn.execute("SELECT scope_json FROM app_entity_scope WHERE entity_id=?", (entity_id,)).fetchone()
    return decode_scope(row["scope_json"]) if row else {}


def decode_scope(raw):
    if raw is None:
        return {}
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 20000:
            raise ValueError("设定可见范围超过支持的大小")
        declaration = json.loads(raw)
        _validate(declaration, FIELDS)
        return normalize_scope(declaration)
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("已保存的设定可见范围损坏，请修复范围后再继续") from error


def _validate(declaration, allowed):
    if not isinstance(declaration, dict) or set(declaration) - allowed:
        raise ValueError("设定可见范围包含不支持的字段")
    for key, value in declaration.items():
        if key == "knowledge_scope":
            _validate(value, SCOPE_FIELDS)
        elif key in LIST_FIELDS:
            if not isinstance(value, list) or len(value) > 100:
                raise ValueError("设定可见范围必须是最多100项的列表")
            for item in value:
                if key == "chapters":
                    if chapter_number(item) is None or not 1 <= chapter_number(item) <= 10000:
                        raise ValueError("可见章节需要在1至10000章之间")
                elif not isinstance(item, str) or not item.strip() or len(item) > 500:
                    raise ValueError("知情人物和分支需要填写有效名称")
        elif key in {"available_from", "valid_start", "valid_end"}:
            if value == "":
                continue
            number = chapter_number(value)
            if number is None or not 1 <= number <= 10000:
                raise ValueError("设定有效章节需要在1至10000章之间")
        elif key == "private":
            if not isinstance(value, bool):
                raise ValueError("设定私密范围需要为开关值")
        elif key == "epistemic_mode":
            if not isinstance(value, str) or value not in EPISTEMIC_MODES:
                raise ValueError("设定信息类型无效")
        elif not isinstance(value, str) or len(value) > 500:
            raise ValueError("设定可见范围文字过长或格式无效")
    start = chapter_number(declaration.get("available_from") or declaration.get("valid_start"))
    end = chapter_number(declaration.get("valid_end"))
    if start is not None and end is not None and end < start:
        raise ValueError("设定结束章节不能早于开始章节")


def normalize_scope(data, previous=None):
    # Legacy clients omit these keys. Only an explicit field replaces a saved
    # boundary, so a title/description-only update cannot reveal a secret.
    declaration = copy.deepcopy(previous or {})
    declaration.update({key: copy.deepcopy(data[key]) for key in FIELDS if key in data})
    _validate(declaration, FIELDS)
    raw = json.dumps(declaration, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if len(raw.encode("utf-8")) > 20000:
        raise ValueError("设定可见范围超过支持的大小")
    return declaration


def write_scope(conn, entity_id, declaration, timestamp):
    conn.execute("INSERT OR REPLACE INTO app_entity_scope(entity_id,scope_json,updated_at) VALUES(?,?,?)",
                 (entity_id, json.dumps(declaration, ensure_ascii=False, sort_keys=True), timestamp))
