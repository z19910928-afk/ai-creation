"""Read-only, identity-bound knowledge links and their current evidence."""
from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from typing import Any

from moyu_context import ScopePolicy, resolve_aliases, scope_decision, source_evidence_reasons
from moyu_records import decode_scope, normalize_scope


MAX_LIMIT = 100
MAX_EVIDENCE_ENTRIES = 100
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MESSAGES = {
    "chapter_not_selected": "尚未选择本章，暂不判断写作可用性",
    "project_scope": "不属于当前作品",
    "branch_scope": "不属于当前分支",
    "missing_source_chapter": "来源章节已删除或不属于当前分支",
    "stale_source": "来源原句需要复核",
    "unusable_status": "资料已撤回、拒绝或失效",
    "unconfirmed_memory": "候选记忆尚未由作者确认",
    "unverified_memory_source": "记忆缺少已核实的原文来源",
    "stale_revision": "来源正文已更新，旧原句需要复核",
    "missing_source_revision": "来源缺少版本记录",
    "invalid_source_quote": "原句、范围或指纹与来源不一致",
    "future_source": "来源位于本章之后",
    "future_reveal": "尚未到设定允许揭示的章节",
    "future_state": "尚未到资料开始有效的章节",
    "expired_state": "资料已超过有效章节",
    "chapter_scope": "资料不在本章允许范围内",
    "future_story_time": "资料发生时间晚于本章",
    "expired_story_time": "资料已超过有效时间",
    "invalid_epistemic_mode": "资料性质需要核对",
    "author_plan": "作者计划或人工关联，不代表已发生事实",
    "unbounded_private_material": "资料仅供作者查看",
    "pov_knowledge": "当前视角人物不知道这条资料",
    "other_character_belief": "这只是其他人物的认知",
    "archived_record": "关联设定已归档",
    "missing_record": "关联设定已不存在",
    "inactive_relationship": "这条关系已停用",
    "unconfirmed_assertion": "这条事实尚未确认或已撤回",
    "source_span_missing": "没有可核实的原文片段",
    "source_memory_missing": "来源记忆已不存在",
    "source_memory_unconfirmed": "来源记忆尚未确认或已撤回",
    "source_memory_mismatch": "记忆与原文片段的编号不一致",
    "source_revision_outdated": "原文片段属于旧正文版本",
    "source_span_invalid": "原文片段已失效或存在歧义",
    "source_not_chapter": "此来源不是可用于本书事实的章节",
    "source_binding_mismatch": "保存的来源信息与原文片段不一致",
    "evidence_error": "来源记录损坏，无法核实",
    "record_error": "关联资料损坏，无法核实可用范围",
    "unsupported_evidence": "旧来源备注不能代替原文证据",
    "too_many_evidence_entries": "来源数量超过本次详情支持范围，需单独核对",
    "pov_knowledge_unannotated": "资料尚未标注哪些人物知道",
}
_EVIDENCE_LABELS = {
    "valid": "原句有效", "stale": "来源需复核",
    "unverified": "尚无已核实来源", "error": "来源读取异常",
}
_STATUS_LABELS = {
    "candidate": "待作者确认", "accepted": "已确认记忆", "canon": "已确认事实",
    "rejected": "已拒绝", "stale": "待复核", "superseded": "已替代",
    "contradicted": "有冲突", "retracted": "已撤回",
}


class KnowledgeNotFound(ValueError):
    """A requested record, work, branch or chapter is outside this scope."""


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label}编号无效")
    return value


def _rows(conn, sql, parameters=()):
    cursor = conn.execute(sql, parameters)
    fields = [column[0] for column in cursor.description]
    return [dict(zip(fields, row)) for row in cursor]


def _one(conn, sql, parameters=()):
    rows = _rows(conn, sql, parameters)
    return rows[0] if rows else None


def _json(raw, expected, label):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as error:
        raise ValueError(f"{label}保存内容损坏") from error
    if not isinstance(value, expected):
        raise ValueError(f"{label}保存格式无效")
    return value


def _messages(reasons):
    return [_MESSAGES.get(reason, "资料需要复核") for reason in reasons]


def _evidence(status, reasons=(), **values):
    reasons = list(dict.fromkeys(reasons))
    return {
        "status": status, "status_label": _EVIDENCE_LABELS[status],
        "stale": status in {"stale", "error"}, "reasons": reasons,
        "reason_messages": _messages(reasons), "source_span_id": None,
        "source_chapter_id": None, "source_chapter_title": "",
        "source_revision_id": None, "current_revision_id": None,
        "source_quote": "", "source_start": None, "source_end": None,
        "source_hash": None, "offset_unit": "unicode_codepoint", **values,
    }


@contextmanager
def _read_snapshot(conn):
    name = "knowledge_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + name)
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT " + name)
        raise
    finally:
        conn.execute("RELEASE SAVEPOINT " + name)


class _Reader:
    def __init__(self, conn, work, branch, chapter):
        self.conn, self.work, self.branch = conn, work, branch
        self.records, self.bodies, self.memories = {}, {}, {}
        if not _one(conn, "SELECT id FROM project WHERE id=?", (work,)):
            raise KnowledgeNotFound("作品不存在")
        if not _one(conn, "SELECT id FROM branch WHERE project_id=? AND id=?", (work, branch)):
            raise KnowledgeNotFound("分支不存在")
        chapters = _rows(conn, """
            SELECT n.id,n.project_id,n.branch_id,n.title,n.ordinal,
              ROW_NUMBER() OVER(ORDER BY n.ordinal,n.id) chapter_number,
              r.id revision_id,m.pov
            FROM document_node n
            JOIN document_revision r ON r.id=(SELECT rr.id FROM document_revision rr
              WHERE rr.document_node_id=n.id AND rr.branch_id=n.branch_id
              ORDER BY rr.rowid DESC LIMIT 1)
            LEFT JOIN app_chapter_meta m ON m.chapter_id=n.id
            WHERE n.project_id=? AND n.branch_id=? AND n.kind='chapter'
              AND NOT EXISTS(SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id)
            ORDER BY n.ordinal,n.id""", (work, branch))
        self.chapters = {row["id"]: row for row in chapters}
        self.revisions = {row["id"]: row["revision_id"] for row in chapters}
        selected = self.chapters.get(chapter) if chapter else None
        if chapter and selected is None:
            raise KnowledgeNotFound("本章已删除或不属于当前作品分支")
        aliases = {"entity_ids": [], "ambiguous": []}
        narration = "limited"
        if selected:
            pov = str(selected.get("pov") or "")
            identities = _rows(conn,
                "SELECT id,canonical_name,aliases_json FROM entity "
                "WHERE project_id=? AND status<>'archived'", (work,))
            aliases = resolve_aliases(identities, pov)
            if pov.strip().lower() in {"omniscient", "全知", "全知视角"}:
                narration = "omniscient"
        self.policy = ScopePolicy(work, branch, selected["chapter_number"] if selected else None,
                                  pov_entity_ids=tuple(aliases["entity_ids"]), narrator_mode=narration)
        self.selected = selected
        self.scope = {
            "project_id": work, "branch_id": branch, "chapter_id": chapter,
            "chapter_number": selected["chapter_number"] if selected else None,
            "chapter_title": selected["title"] if selected else "",
            "pov_entity_ids": aliases["entity_ids"], "narrator_mode": narration,
            "ambiguous_aliases": aliases["ambiguous"],
        }

    def record(self, identifier):
        if identifier not in self.records:
            row = _one(self.conn, """
                SELECT e.id,e.project_id,e.kind,e.canonical_name,e.aliases_json,e.status,
                  COALESCE(m.description,'') description,s.scope_json
                FROM entity e LEFT JOIN app_entity_meta m ON m.entity_id=e.id
                LEFT JOIN app_entity_scope s ON s.entity_id=e.id
                WHERE e.project_id=? AND e.id=?""", (self.work, identifier))
            if row is None:
                self.records[identifier] = None
            else:
                aliases = _json(row["aliases_json"], (list, dict), "设定别名")
                if isinstance(aliases, dict):
                    aliases = aliases.get("aliases", [])
                if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
                    raise ValueError("设定别名保存格式无效")
                self.records[identifier] = {
                    "id": row["id"], "project_id": row["project_id"], "type": row["kind"],
                    "name": row["canonical_name"], "aliases": aliases, "status": row["status"],
                    "description": row["description"], **decode_scope(row["scope_json"]),
                }
        return self.records[identifier]

    def availability(self, item, kind, *, evidence=None, extra=(), dependents=()):
        decision = scope_decision(item, self.policy, kind=kind,
                                  chapters=self.chapters, revisions=self.revisions)
        reasons = list(decision["reasons"]) + list(extra)
        warnings = list(decision["warnings"])
        if not self.selected:
            reasons.append("chapter_not_selected")
        if item.get("status") == "archived":
            reasons.append("archived_record")
        if evidence and evidence["status"] in {"stale", "error"}:
            reasons.extend(evidence["reasons"] or ["stale_source"])
        for dependent in dependents:
            reasons.extend(dependent["reasons"])
            warnings.extend(dependent["warnings"])
        reasons, warnings = list(dict.fromkeys(reasons)), list(dict.fromkeys(warnings))
        status = "unselected" if not self.selected else "restricted" if reasons else "available"
        return {
            **decision, "allowed": not reasons, "reasons": reasons, "warnings": warnings,
            "reason_messages": _messages(reasons), "warning_messages": _messages(warnings),
            "status": status, "status_label": {
                "available": "本章可用", "restricted": "本章不可用",
                "unselected": "尚未选择本章",
            }[status],
        }

    def memory(self, identifier):
        if identifier not in self.memories:
            self.memories[identifier] = _one(self.conn,
                "SELECT * FROM candidate_memory WHERE project_id=? AND branch_id=? AND id=?",
                (self.work, self.branch, identifier))
        return self.memories[identifier]

    def span(self, identifier, supplied, *declarations):
        row = _one(self.conn, """
            SELECT s.*,n.title source_chapter_title,n.kind source_kind,
              EXISTS(SELECT 1 FROM app_deleted_node d WHERE d.chapter_id=n.id) deleted,
              (SELECT rr.id FROM document_revision rr WHERE rr.document_node_id=n.id
                AND rr.branch_id=n.branch_id ORDER BY rr.rowid DESC LIMIT 1) current_revision_id
            FROM source_span s JOIN document_node n ON n.id=s.document_node_id
            WHERE s.id=? AND s.project_id=? AND n.project_id=? AND n.branch_id=?""",
            (identifier, self.work, self.work, self.branch))
        if row is None:
            return _evidence("stale", ["source_span_missing"], source_span_id=identifier)
        values = {
            "source_span_id": row["id"], "source_chapter_id": row["document_node_id"],
            "source_chapter_title": row["source_chapter_title"],
            "source_revision_id": row["revision_id"], "current_revision_id": row["current_revision_id"],
            "source_quote": row["quote"], "source_start": row["start_codepoint"],
            "source_end": row["end_codepoint"], "source_hash": row["quote_hash"],
        }
        reasons = []
        if row["deleted"]:
            reasons.append("missing_source_chapter")
        if row["source_kind"] != "chapter":
            reasons.append("source_not_chapter")
        if row["status"] != "valid":
            reasons.append("source_span_invalid")
        if row["revision_id"] != row["current_revision_id"]:
            reasons.append("source_revision_outdated")
        if row["document_node_id"] in self.chapters:
            chapter = self.chapters[row["document_node_id"]]
            if chapter["id"] not in self.bodies:
                revision = _one(self.conn, "SELECT canonical_text FROM document_revision WHERE id=?",
                                (chapter["revision_id"],))
                raw = revision["canonical_text"]
                self.bodies[chapter["id"]] = bytes(raw).decode("utf-8") if not isinstance(raw, str) else raw
            chapter["body"] = self.bodies[chapter["id"]]
            reasons.extend(source_evidence_reasons(
                row, self.bodies[chapter["id"]], supplied, *declarations))
        elif not reasons:
            reasons.append("missing_source_chapter")
        return _evidence("stale" if reasons else "valid", reasons, **values)

    def evidence(self, raw, *declarations):
        if not raw:
            return _evidence("unverified", ["source_span_missing"])
        if not isinstance(raw, dict):
            return _evidence("unverified", ["unsupported_evidence"])
        identifier = raw.get("source_span_id")
        if not identifier:
            return _evidence("unverified", ["source_span_missing"])
        return self.span(_id(identifier, "来源片段"), raw, *declarations)

    def relationship(self, row, primary):
        other_id = row["to_entity_id"] if row["from_entity_id"] == primary["id"] else row["from_entity_id"]
        errors, extra = [], []
        try:
            other = self.record(other_id)
        except (ValueError, UnicodeError) as error:
            identity = _one(self.conn,
                "SELECT id,kind,canonical_name,status FROM entity WHERE project_id=? AND id=?",
                (self.work, other_id))
            other = ({"id": identity["id"], "type": identity["kind"], "name": identity["canonical_name"],
                      "status": identity["status"], "error": str(error)} if identity else None)
            errors.append(str(error))
            extra.append("record_error")
        if other is None:
            other = {"id": other_id, "type": "", "name": "关联设定已不存在", "status": "missing"}
            extra.append("missing_record")
        try:
            raw = _json(row["evidence_json"], dict, "关系来源")
            evidence = self.evidence(raw)
            declaration = normalize_scope(raw)
        except (ValueError, UnicodeError) as error:
            raw, declaration = {}, {}
            evidence = _evidence("error", ["evidence_error"], error=str(error))
            errors.append(str(error))
        epistemic = raw.get("epistemic_mode", "world_truth") if evidence["status"] != "unverified" else "author_plan"
        item = {
            **declaration, "id": row["id"], "project_id": self.work, "branch_id": self.branch,
            "status": row["status"], "epistemic_mode": epistemic,
            **{key: value for key, value in evidence.items() if key.startswith("source_")},
        }
        if row["status"] != "active":
            extra.append("inactive_relationship")
        return {
            "id": row["id"], "from_entity_id": row["from_entity_id"],
            "to_entity_id": row["to_entity_id"], "relation_type": row["relation_type"],
            "note": row["note"], "status": row["status"], "epistemic_mode": epistemic,
            "other_record": {key: value for key, value in other.items() if key != "description"},
            "evidence": evidence, "errors": errors,
            "availability": self.availability(item, "relationship", evidence=evidence, extra=extra,
                dependents=(self.availability(primary, "setting"), self.availability(other, "setting"))),
        }

    def source(self, kind, identifier, primary):
        primary_availability = self.availability(primary, "setting")
        if kind == "memory":
            row = self.memory(identifier)
            payload = _json(row["payload_json"], dict, "记忆")
            declaration = normalize_scope(payload)
            evidence = self.evidence({**payload, "source_span_id": row["source_span_id"]}, payload)
            item = {
                **declaration, **payload, "id": identifier, "project_id": self.work,
                "branch_id": self.branch, "status": row["status"],
                **{key: value for key, value in evidence.items() if key.startswith("source_")},
                "source_span_status": "valid" if evidence["status"] == "valid" else "stale",
                "stale": evidence["stale"],
            }
            availability = self.availability(item, "memory", evidence=evidence,
                                             dependents=(primary_availability,))
            name, status = str(payload.get("name") or payload.get("text") or "未命名记忆"), row["status"]
        else:
            row = _one(self.conn, "SELECT * FROM assertion WHERE project_id=? AND branch_id=? AND id=?",
                       (self.work, self.branch, identifier))
            payload = _json(row["object_json"], (dict, list, str, int, float, bool, type(None)), "事实内容")
            entries = _json(row["evidence_json"], list, "事实来源")
            if len(entries) > MAX_EVIDENCE_ENTRIES:
                evidence = _evidence("error", ["too_many_evidence_entries"], entries=[], entries_total=len(entries))
                dependents = []
            else:
                evidence, dependents = self.assertion_evidence(entries)
            item = {
                "id": identifier, "project_id": self.work, "branch_id": self.branch,
                "status": row["state"], "epistemic_mode": row["epistemic_mode"],
                "valid_start": row["valid_start"], "valid_end": row["valid_end"],
                **{key: value for key, value in evidence.items() if key.startswith("source_")},
            }
            extra = [] if row["state"] == "canon" else ["unconfirmed_assertion"]
            if evidence["status"] == "unverified":
                extra.append("source_span_missing")
            availability = self.availability(item, "assertion", evidence=evidence,
                                             extra=extra, dependents=(*dependents, primary_availability))
            name = str(payload.get("name") or row["predicate"]) if isinstance(payload, dict) else row["predicate"]
            status = row["state"]
        return {
            "kind": kind, "kind_label": "记忆" if kind == "memory" else "事实",
            "id": identifier, "name": name, "status": status,
            "status_label": _STATUS_LABELS.get(status, "状态待核对"),
            "source_chapter_title": evidence["source_chapter_title"],
            "evidence": evidence, "availability": availability,
        }

    def assertion_evidence(self, entries):
        resolved, dependents, reasons = [], [], []
        referenced = False
        for raw in entries:
            if not isinstance(raw, dict) or not any(key in raw for key in ("source_span_id", "candidate_memory_id")):
                continue
            referenced = True
            child = self.evidence(raw)
            if raw.get("candidate_memory_id"):
                memory = self.memory(_id(raw["candidate_memory_id"], "来源记忆"))
                if memory is None:
                    child = _evidence("stale", [*child["reasons"], "source_memory_missing"],
                                      **{key: value for key, value in child.items()
                                         if key not in {"status", "status_label", "stale", "reasons", "reason_messages"}})
                else:
                    payload = _json(memory["payload_json"], dict, "来源记忆")
                    declaration = normalize_scope(payload)
                    if memory["status"] != "accepted":
                        reasons.append("source_memory_unconfirmed")
                    if memory["source_span_id"] != raw.get("source_span_id"):
                        reasons.append("source_memory_mismatch")
                    memory_evidence = self.evidence(
                        {**payload, "source_span_id": memory["source_span_id"]}, payload)
                    if memory_evidence["status"] != "valid":
                        reasons.extend(memory_evidence["reasons"] or ["source_span_missing"])
                    dependents.append(self.availability({
                        **declaration, "status": memory["status"], "project_id": self.work,
                        "branch_id": self.branch,
                    }, "setting"))
                    if memory["entity_id"]:
                        owner = self.record(memory["entity_id"])
                        if owner is None:
                            reasons.append("missing_record")
                        else:
                            dependents.append(self.availability(owner, "setting"))
            elif "candidate_memory_id" in raw:
                reasons.append("source_memory_missing")
            if child["status"] != "valid":
                reasons.extend(child["reasons"] or ["source_span_missing"])
            resolved.append(child)
        if not referenced:
            return _evidence("unverified", ["unsupported_evidence"] if entries else ["source_span_missing"],
                             entries=[], entries_total=len(entries)), dependents
        latest = max(resolved, key=lambda child:
                     self.chapters.get(child["source_chapter_id"], {}).get("chapter_number", -1))
        values = {key: value for key, value in latest.items()
                  if key not in {"status", "status_label", "stale", "reasons", "reason_messages"}}
        return _evidence("stale" if reasons else "valid", reasons, **values,
                         entries=resolved, entries_total=len(entries)), dependents


def record_knowledge(conn, work_id, branch_id, record_id, chapter_id=None, *, limit=50, offset=0):
    """Aggregate a bounded author view without changing model material or state."""
    work, branch = _id(work_id, "作品"), _id(branch_id, "分支")
    record_id = _id(record_id, "设定")
    chapter = _id(chapter_id, "章节") if chapter_id not in (None, "") else None
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"每页数量需要在1至{MAX_LIMIT}之间")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
        raise ValueError("分页位置需要是0至1000000之间的整数")
    with _read_snapshot(conn):
        return _record_knowledge(conn, work, branch, record_id, chapter, limit, offset)


def _record_knowledge(conn, work, branch, record_id, chapter, limit, offset):
    reader = _Reader(conn, work, branch, chapter)
    record = reader.record(record_id)
    if record is None or record["status"] == "archived":
        raise KnowledgeNotFound("设定不存在或已归档")
    result = {
        "record": record, "scope": reader.scope, "availability": reader.availability(record, "setting"),
        "outgoing": [], "incoming": [], "sources": [], "summary": {}, "errors": [],
        "pagination": {"limit": limit, "offset": offset},
    }
    for name, field in (("outgoing", "from_entity_id"), ("incoming", "to_entity_id")):
        total = _one(conn, f"SELECT COUNT(*) total FROM app_story_relation "
                          f"WHERE project_id=? AND branch_id=? AND {field}=?",
                     (work, branch, record_id))["total"]
        rows = _rows(conn, f"SELECT * FROM app_story_relation "
                          f"WHERE project_id=? AND branch_id=? AND {field}=? "
                          "ORDER BY id LIMIT ? OFFSET ?", (work, branch, record_id, limit, offset))
        result[name] = [reader.relationship(row, record) for row in rows]
        result["summary"][name] = total
    sources_sql = """
        SELECT 'memory' kind,id FROM candidate_memory
          WHERE project_id=? AND branch_id=? AND entity_id=?
        UNION ALL
        SELECT 'assertion' kind,id FROM assertion
          WHERE project_id=? AND branch_id=? AND subject_entity_id=?"""
    parameters = (work, branch, record_id, work, branch, record_id)
    result["summary"]["sources"] = _one(conn, f"SELECT COUNT(*) total FROM ({sources_sql})", parameters)["total"]
    for row in _rows(conn, sources_sql + " ORDER BY kind,id LIMIT ? OFFSET ?", (*parameters, limit, offset)):
        try:
            result["sources"].append(reader.source(row["kind"], row["id"], record))
        except (ValueError, UnicodeError) as error:
            evidence = _evidence("error", ["evidence_error"], error=str(error))
            result["sources"].append({
                "kind": row["kind"], "kind_label": "记忆" if row["kind"] == "memory" else "事实",
                "id": row["id"], "name": "来源记录需要修复", "status": "error", "status_label": "读取异常",
                "source_chapter_title": "", "evidence": evidence,
                "availability": reader.availability({}, row["kind"], evidence=evidence),
            })
            result["errors"].append({"kind": row["kind"], "id": row["id"], "message": str(error)})
    for name in ("outgoing", "incoming", "sources"):
        returned, total = len(result[name]), result["summary"][name]
        more = offset + returned < total
        result["pagination"][name] = {
            "total": total, "returned": returned, "has_more": more,
            "next_offset": offset + returned if more else None,
        }
    return result
