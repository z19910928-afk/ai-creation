"""Local, source-grounded retrieval and author-reviewed story analysis.

All functions are independent of a database, model provider and global project
state. Offsets are Unicode codepoints into the exact supplied revision. Model
results remain candidates after validation: an exact quote establishes a
source, not the truth of an interpretation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
import copy
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping


DEFAULT_PROFILE = {
    "max_tokens": 24_000,
    "chapter_top_k": 20,
    "recent_window": 3,
    "memory_top_k": 60,
    "record_top_k": 40,
}
PROFILE_LIMITS = {
    "max_tokens": (4_000, 64_000),
    "chapter_top_k": (0, 50),
    "recent_window": (0, 10),
    "memory_top_k": (0, 120),
    "record_top_k": (0, 80),
}
EPISTEMIC_MODES = {"world_truth", "character_belief", "narrator", "inference", "author_plan"}
CANDIDATE_KINDS = {"fact", "event", "character_state", "relationship", "foreshadowing"}
REVIEW_CATEGORIES = {"continuity", "motivation", "causality", "time", "state", "foreshadowing", "pov", "pacing", "style"}
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z0-9_]+")
_COMMON_GRAMS = {
    "一个", "一种", "一下", "什么", "这个", "那个", "他们", "她们", "我们",
    "可以", "没有", "已经", "还是", "自己", "时候", "这里", "那里", "为了",
    "因为", "所以", "以及", "然后", "之后", "之前", "继续", "小说", "章节",
    "要求", "场景", "正文", "人物", "故事",
}
_BODY_TASKS = {"polish", "summary", "check", "chapter_analysis", "semantic_review"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_profile(raw: Mapping[str, Any] | None = None) -> dict[str, int]:
    values = dict(DEFAULT_PROFILE)
    if raw is None:
        return values
    if not isinstance(raw, Mapping):
        raise ValueError("retrieval_profile must be an object")
    for name, value in raw.items():
        if name not in values or isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"invalid retrieval_profile.{name}")
        lower, upper = PROFILE_LIMITS[name]
        values[name] = max(lower, min(upper, value))
    return values


def chapter_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(?:(?:chapter|ch)[-_ ]*|第\s*)?(\d+)(?:\s*章)?\s*", value, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _strings(value: Any) -> set[str]:
    return {str(item) for item in value if isinstance(item, (str, int))} if isinstance(value, (list, tuple, set)) else set()


def _knowledge_owners(item: Mapping[str, Any], scope: Mapping[str, Any]) -> set[str] | None:
    constraints: list[set[str]] = []
    for declaration, fields in ((item, ("known_by",)), (scope, ("known_by", "characters", "entities"))):
        for field in fields:
            if field in declaration:
                owners = _strings(declaration[field])
                if owners or declaration.get("private"):
                    constraints.append(owners)
    # None is unannotated; an empty intersection explicitly permits no POV.
    if constraints:
        return set.intersection(*constraints)
    return set() if item.get("private") or scope.get("private") else None


@dataclass(frozen=True)
class ScopePolicy:
    project_id: str
    branch_id: str
    chapter_upper: int | None = None
    story_time: str | None = None
    pov_entity_ids: tuple[str, ...] = ()
    narrator_mode: str = "limited"
    allow_plans: bool = False
    include_current: bool = True


def scope_decision(
    item: Mapping[str, Any],
    policy: ScopePolicy,
    *,
    kind: str = "",
    chapters: Mapping[str, Mapping[str, Any]] | None = None,
    revisions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Apply the same hard scope checks before every material is ranked."""
    scope = _object(item.get("knowledge_scope") or item.get("knowledge_scope_json") or item.get("scope"))
    reasons: list[str] = []
    warnings: list[str] = []
    for field, wanted in (("project_id", policy.project_id), ("branch_id", policy.branch_id)):
        declared = item.get(field, scope.get(field))
        if declared is not None and str(declared) != wanted:
            reasons.append("project_scope" if field == "project_id" else "branch_scope")
    branches = _strings(scope.get("branches") or scope.get("branch_ids"))
    if branches and policy.branch_id not in branches:
        reasons.append("branch_scope")
    source_id = str(item.get("source_chapter_id") or item.get("chapter_id") or "")
    source_chapter = chapters.get(source_id) if chapters and source_id else None
    if source_id and chapters is not None and source_chapter is None:
        reasons.append("missing_source_chapter")
    if item.get("stale") or item.get("deleted") or item.get("source_span_status") in {"stale", "invalid", "orphaned"}:
        reasons.append("stale_source")
    status = str(item.get("status_raw") or item.get("status") or "")
    if status in {"rejected", "superseded", "stale", "retracted", "已拒绝", "待复核"}:
        reasons.append("unusable_status")
    if kind == "memory" and status not in {"accepted", "已确认", "canon"}:
        reasons.append("unconfirmed_memory")
    if kind == "memory" and (not item.get("source_span_id") or item.get("source_span_status") != "valid"):
        reasons.append("unverified_memory_source")
    supplied_revision = item.get("source_revision_id")
    current_revision = (revisions or {}).get(source_id) or item.get("current_revision_id")
    if supplied_revision and current_revision and str(supplied_revision) != str(current_revision):
        reasons.append("stale_revision")
    if kind == "memory" and current_revision and not supplied_revision:
        reasons.append("missing_source_revision")
    if kind == "memory" and source_chapter is not None:
        try:
            validate_source_quote(item, source_chapter, str(current_revision or supplied_revision or ""))
        except ValueError:
            reasons.append("invalid_source_quote")
    ordinal = chapter_number(item.get("chapter_number") or item.get("chapter_ordinal"))
    if source_chapter is not None:
        ordinal = chapter_number(source_chapter.get("chapter_number") or source_chapter.get("chapter_ordinal")) or ordinal
    available = chapter_number(scope.get("available_from") or item.get("available_from"))
    valid_start = chapter_number(item.get("valid_start") or scope.get("valid_start"))
    valid_end = chapter_number(item.get("valid_end") or scope.get("valid_end"))
    upper = policy.chapter_upper
    if upper is not None:
        if ordinal is not None and (ordinal > upper or (ordinal == upper and not policy.include_current)):
            reasons.append("future_source")
        if available is not None and available > upper:
            reasons.append("future_reveal")
        if valid_start is not None and valid_start > upper:
            reasons.append("future_state")
        if valid_end is not None and valid_end < upper:
            reasons.append("expired_state")
        declared_chapters = _strings(scope.get("chapters"))
        if declared_chapters and upper not in {chapter_number(value) for value in declared_chapters}:
            reasons.append("chapter_scope")
    time_start = str(item.get("story_time") or item.get("event_time") or scope.get("story_time") or scope.get("time_from") or "")
    time_end = str(scope.get("time_to") or "")
    if policy.story_time:
        if time_start and time_start > policy.story_time:
            reasons.append("future_story_time")
        if time_end and time_end < policy.story_time:
            reasons.append("expired_story_time")
    mode = str(item.get("epistemic_mode") or scope.get("epistemic_mode") or "world_truth")
    if mode not in EPISTEMIC_MODES:
        reasons.append("invalid_epistemic_mode")
    plan = mode == "author_plan" or kind == "plan" or status in {"planned", "plan", "计划", "待发生"}
    if plan and not policy.allow_plans:
        reasons.append("author_plan")
    known_by = _knowledge_owners(item, scope)
    pov = set(policy.pov_entity_ids)
    if policy.narrator_mode != "omniscient":
        if not known_by and (item.get("private") or scope.get("private")):
            reasons.append("unbounded_private_material")
        if known_by is not None and not pov.intersection(known_by):
            reasons.append("pov_knowledge")
        elif mode == "character_belief":
            subject = str(item.get("entity_id") or item.get("subject_entity_id") or scope.get("subject_entity_id") or "")
            if known_by is None and subject not in pov:
                reasons.append("other_character_belief")
        elif item.get("private") or scope.get("private"):
            if known_by is None:
                reasons.append("unbounded_private_material")
        elif known_by is None and kind in {"chapter", "memory", "character_state", "timeline", "relationship"}:
            warnings.append("pov_knowledge_unannotated")
    return {"allowed": not reasons, "reasons": list(dict.fromkeys(reasons)), "warnings": warnings, "epistemic_mode": mode, "is_plan": plan}


@lru_cache(maxsize=4096)
def lexical_terms(text: str) -> tuple[str, ...]:
    """Overlapping Chinese grams retain names inside ordinary sentences."""
    tokens = [word.casefold() for word in _LATIN.findall(text) if len(word) >= 2]
    for run in _CJK.findall(text):
        if len(run) == 1:
            continue
        for size in (2, 3):
            tokens.extend(run[index:index + size] for index in range(len(run) - size + 1) if run[index:index + size] not in _COMMON_GRAMS)
    return tuple(tokens)


def resolve_aliases(records: Iterable[Mapping[str, Any]], query: str) -> dict[str, Any]:
    owners: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    aliases_by_entity: dict[str, set[str]] = {}
    for record in records:
        entity_id = str(record.get("id") or "")
        name = str(record.get("name") or record.get("canonical_name") or "")
        if not entity_id or not name:
            continue
        raw_aliases = record.get("aliases", record.get("aliases_json", []))
        if isinstance(raw_aliases, str):
            try:
                raw_aliases = json.loads(raw_aliases)
            except ValueError:
                raw_aliases = []
        aliases = _strings(raw_aliases) | {name}
        names[entity_id] = name
        aliases_by_entity[entity_id] = aliases
        for alias in aliases:
            if alias:
                owners[alias.casefold()].add(entity_id)
    matched: set[str] = set()
    ambiguous: list[dict[str, Any]] = []
    folded = query.casefold()
    for alias, entity_ids in owners.items():
        if _LATIN.fullmatch(alias):
            present = re.search(r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])", folded) is not None
        else:
            present = alias in folded
        if not present:
            continue
        if len(entity_ids) == 1:
            matched.update(entity_ids)
        else:
            ambiguous.append({"alias": alias, "entity_ids": sorted(entity_ids)})
    expanded = sorted({alias for entity_id in matched for alias in aliases_by_entity[entity_id]})
    return {"entity_ids": sorted(matched), "terms": expanded, "ambiguous": ambiguous, "names": names}


def paragraph_ranges(body: str, max_chars: int = 900) -> list[tuple[int, int]]:
    """Create bounded exact slices; long paragraphs split at sentence ends."""
    if max_chars < 80:
        raise ValueError("max_chars must be at least 80")
    ranges: list[tuple[int, int]] = []
    for match in re.finditer(r"[^\r\n]+(?:\r?\n|$)", body):
        start, end = match.span()
        while start < end:
            split = min(end, start + max_chars)
            if split < end:
                punctuation = [found.end() for found in re.finditer(r"[。！？!?；;]", body[start:split])]
                if punctuation and punctuation[-1] >= max_chars // 2:
                    split = start + punctuation[-1]
            if body[start:split].strip():
                ranges.append((start, split))
            start = split
    return ranges


def chapter_chunks(chapter: Mapping[str, Any], revision_id: str = "") -> list[dict[str, Any]]:
    body = str(chapter.get("body") or "")
    chapter_id = str(chapter.get("id") or "")
    revision = revision_id or str(chapter.get("revision_id") or chapter.get("source_revision_id") or "")
    digest = text_hash(body)
    chunks: list[dict[str, Any]] = []
    source_scopes = chapter.get("source_scopes") if isinstance(chapter.get("source_scopes"), list) else []
    for index, (start, end) in enumerate(paragraph_ranges(body)):
        item = {
            **{key: chapter[key] for key in ("project_id", "branch_id", "chapter_number", "chapter_ordinal", "knowledge_scope", "known_by", "private", "epistemic_mode", "story_time") if key in chapter},
            "id": f"{chapter_id}:{revision or digest[:12]}:{start}:{end}",
            "kind": "chapter", "chapter_id": chapter_id, "source_chapter_id": chapter_id,
            "title": str(chapter.get("title") or ""), "body": body[start:end],
            "source_quote": body[start:end], "source_start": start, "source_end": end,
            "source_revision_id": revision, "chapter_hash": digest, "source_hash": text_hash(body[start:end]),
            "chunk_index": index,
        }
        # A chunk overlapping a restricted scene inherits that restriction.
        # Several incompatible private scenes are conservatively excluded.
        restrictions = []
        for scoped in source_scopes:
            if not isinstance(scoped, Mapping):
                continue
            left, right = scoped.get("start"), scoped.get("end")
            if isinstance(left, int) and isinstance(right, int) and left < end and right > start:
                restrictions.append(_object(scoped.get("knowledge_scope") or scoped.get("scope")))
        if restrictions:
            base_scope = _object(item.get("knowledge_scope"))
            scope_owners = [_knowledge_owners({}, scope) or set() for scope in restrictions]
            chapter_owners = _knowledge_owners(item, base_scope)
            if chapter_owners is not None:
                scope_owners.append(chapter_owners)
            known = set.intersection(*scope_owners)
            base_scope.update({"known_by": sorted(known), "private": True})
            item["known_by"] = sorted(known)
            item["knowledge_scope"] = base_scope
        chunks.append(item)
    return chunks


def search_materials(
    materials: Iterable[Mapping[str, Any]],
    query: str,
    policy: ScopePolicy,
    *,
    records: Iterable[Mapping[str, Any]] = (),
    chapters: Mapping[str, Mapping[str, Any]] | None = None,
    revisions: Mapping[str, str] | None = None,
    limit: int = 20,
    query_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Rank permitted paragraph/material documents using local BM25."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    aliases = resolve_aliases(records, query)
    expanded = query + " " + " ".join(aliases["terms"])
    query_terms = set(lexical_terms(expanded))
    weights = dict(query_weights or {})
    for term in lexical_terms(" ".join(aliases["terms"])):
        weights[term] = max(weights.get(term, 1.0), 2.0)
    visible: list[tuple[dict[str, Any], dict[str, Any], Counter[str]]] = []
    excluded: list[dict[str, Any]] = []
    for material in materials:
        item = dict(material)
        decision = scope_decision(item, policy, kind=str(item.get("kind") or ""), chapters=chapters, revisions=revisions)
        if not decision["allowed"]:
            excluded.append({"id": item.get("id"), "kind": item.get("kind"), "reasons": decision["reasons"]})
            continue
        text = " ".join(str(item.get(key) or "") for key in ("title", "body", "name", "note", "description", "source_quote"))
        visible.append((item, decision, Counter(lexical_terms(text))))
    if not limit or not query_terms or not visible:
        return {"results": [], "excluded": excluded, "aliases": aliases, "query": expanded, "method": "local_bm25_cjk"}
    frequencies = Counter(term for _, _, terms in visible for term in query_terms.intersection(terms))
    average = sum(sum(terms.values()) for _, _, terms in visible) / len(visible) or 1.0
    ranked: list[dict[str, Any]] = []
    for item, decision, terms in visible:
        length = sum(terms.values())
        score = 0.0
        matched = sorted(query_terms.intersection(terms))
        for term in matched:
            frequency = terms[term]
            inverse = math.log(1 + (len(visible) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += weights.get(term, 1.0) * inverse * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average))
        if not score:
            continue
        ranked.append({
            **item, "relevance": round(score, 6), "retrieval_layer": "lexical",
            "retrieval_reason": {"method": "local_bm25_cjk", "matched_terms": matched[:30], "source_warnings": decision["warnings"]},
        })
    ranked.sort(key=lambda item: (-item["relevance"], -int(item.get("chapter_number") or 0), str(item.get("id") or "")))
    return {"results": ranked[:limit], "excluded": excluded, "aliases": aliases, "query": expanded, "method": "local_bm25_cjk"}


def _evidence_item(chapter: Mapping[str, Any], start: int, end: int, revision: str, layer: str) -> dict[str, Any]:
    body = str(chapter.get("body") or "")
    quote = body[start:end]
    source_range = {"start": start, "end": end}
    return {
        "id": chapter["id"], "chapter": chapter.get("chapter_number"), "title": chapter.get("title", ""),
        "excerpt": quote, "retrieval_layer": layer,
        "source_range": source_range, "source_ranges": [source_range],
        "source_revision_id": revision, "full_content": start == 0 and end == len(body),
        "evidence": {
            "source_chapter_id": chapter["id"], "source_revision_id": revision, "source_range": source_range,
            "source_ranges": [source_range], "source_quote": quote, "source_hash": text_hash(quote),
            "chapter_hash": text_hash(body), "offset_unit": "unicode_codepoint",
        },
    }


def _controls(items: list[dict[str, Any]], choices: Any) -> list[dict[str, Any]]:
    if not isinstance(choices, Mapping):
        return items
    result = []
    for item in items:
        control = choices.get(str(item.get("id"))) or {}
        if not isinstance(control, Mapping) or control.get("omit"):
            if isinstance(control, Mapping) and control.get("omit"):
                continue
            control = {}
        value = {**item, "_pinned": bool(control.get("pin"))}
        order = control.get("order")
        value["_order"] = order if isinstance(order, int) and not isinstance(order, bool) else 10_000
        result.append(value)
    result.sort(key=lambda item: (not item["_pinned"], item["_order"]))
    return result


def fit_budget(context: dict[str, Any], max_tokens: int, *, reserve_bytes: int = 1800) -> dict[str, Any]:
    """Use a conservative UTF-8 guard, never pretend chars/4 is an upper bound.

    Exact provider token counts are available only from the selected provider.
    This local byte estimate is intentionally conservative for common
    byte-based tokenizers and includes a fixed reserve for task instructions.
    """
    allowance = max_tokens - reserve_bytes
    if allowance < 1000:
        raise ValueError("context budget is too small")
    meta = context.setdefault("context_budget", {})
    truncation = {"applied": False, "removed": [], "clipped": [], "pinned": []}
    meta.update({
        "max_tokens": max_tokens, "max_context_utf8_bytes": allowance,
        "prompt_reserve_bytes": reserve_bytes, "token_estimator": "utf8_bytes_conservative_estimate",
        "token_count_exact": False, "actual_input_tokens": None, "truncation": truncation,
    })
    for key in ("retrieved_chapters", "previous_chapters", "confirmed_memory", "story_cards", "author_plans"):
        truncation["pinned"].extend(str(item.get("id")) for item in context.get(key, []) if item.get("_pinned"))
    meta.update({"estimated_chars": 0, "estimated_tokens": 0, "estimated_utf8_bytes": 0})
    def refresh_counts():
        ids = [str(item["id"]) for key in ("retrieved_chapters", "previous_chapters", "confirmed_memory", "story_cards", "author_plans") for item in context.get(key, []) if item.get("id")]
        meta["context_item_ids"] = list(dict.fromkeys(ids))
        meta["retrieved_count"] = len(context.get("retrieved_chapters", []))
        meta["previous_count"] = len(context.get("previous_chapters", []))
        meta["retrieval_tiers"] = {
            "lexical": sum(item.get("retrieval_layer") == "lexical" for item in context.get("retrieved_chapters", [])),
            "recency": len(context.get("previous_chapters", [])),
            "evidence": len(context.get("confirmed_memory", [])),
            "record": len(context.get("story_cards", [])),
            "author_plan": len(context.get("author_plans", [])),
        }
    def serialized_size() -> int:
        refresh_counts()
        return len(_json(context).encode("utf-8"))
    if serialized_size() + 30 > allowance:
        # Diagnostic details are optional prompt data; exact manuscript and
        # source ranges are not. Counts expose any diagnostic compaction.
        chapter = context.get("current_chapter")
        if isinstance(chapter, dict) and isinstance(chapter.get("plan"), dict):
            chapter["plan"] = {key: value for key, value in chapter["plan"].items() if value}
        if isinstance(context.get("project"), dict):
            context["project"] = {key: value for key, value in context["project"].items() if value not in ("", None)}
        scope = meta.get("knowledge_scope", {})
        for name in ("filtered", "warnings"):
            details = scope.get(name, [])
            if details:
                kept = [item for item in details if item.get("kind") != "chapter"]
                scope[name + "_details_omitted"] = len(details) - len(kept)
                scope[name] = kept
    target = allowance - 30
    minimums = {
        "retrieved_chapters": 2, "previous_chapters": 0, "confirmed_memory": 8,
        "story_cards": 4, "author_plans": 4,
    }
    def pop_unpinned(key: str, minimum: int = 0) -> tuple[str, dict[str, Any]] | None:
        items = context.get(key, [])
        if len(items) <= minimum:
            return None
        for index in range(len(items) - 1, -1, -1):
            if not items[index].get("_pinned"):
                return key, items.pop(index)
        return None
    while serialized_size() > target:
        removed = None
        # Remove distant low-ranked prose before confirmed facts and the
        # small active setting set. Otherwise a broad name query can evict all
        # evidence in favour of many interchangeable old paragraphs.
        for key in ("previous_chapters", "retrieved_chapters", "story_cards", "confirmed_memory", "author_plans"):
            removed = pop_unpinned(key, minimums[key])
            if removed:
                break
        if not removed:
            for key, items in context.get("story_structure", {}).items():
                if len(items) > 4:
                    removed = ("story_structure." + key, items.pop())
                    break
        if not removed:
            for key in ("author_plans", "story_cards", "retrieved_chapters", "confirmed_memory"):
                removed = pop_unpinned(key)
                if removed:
                    break
        if not removed:
            for key, items in context.get("story_structure", {}).items():
                if items:
                    removed = ("story_structure." + key, items.pop())
                    break
        if not removed:
            raise ValueError("current chapter or pinned material exceeds the conservative prompt budget; select a passage or increase the context budget")
        truncation["applied"] = True
        truncation["removed"].append({"kind": removed[0], "id": removed[1].get("id"), "reason": "conservative_prompt_budget"})
    for key in ("retrieved_chapters", "previous_chapters", "confirmed_memory", "story_cards", "author_plans"):
        for item in context.get(key, []):
            item.pop("_pinned", None)
            item.pop("_order", None)
    refresh_counts()
    # Metadata is sent too. Fixed-size integer fields are solved to stability.
    meta.update({"estimated_chars": 0, "estimated_tokens": 0, "estimated_utf8_bytes": 0})
    for _ in range(6):
        encoded = _json(context)
        size = len(encoded.encode("utf-8"))
        old = (meta["estimated_chars"], meta["estimated_tokens"], meta["estimated_utf8_bytes"])
        meta.update({"estimated_chars": len(encoded), "estimated_tokens": size + reserve_bytes, "estimated_utf8_bytes": size})
        if old == (meta["estimated_chars"], meta["estimated_tokens"], meta["estimated_utf8_bytes"]):
            break
    if serialized_size() > allowance:
        raise ValueError("material metadata exceeds the conservative prompt budget; reduce pinned materials")
    return context


def assemble_context(
    state: Mapping[str, Any],
    chapter_id: str | None,
    *,
    structure: Mapping[str, Any] | None = None,
    instruction: str = "",
    profile: Mapping[str, Any] | None = None,
    controls: Mapping[str, Any] | None = None,
    project_id: str = "",
    branch_id: str = "",
    revisions: Mapping[str, str] | None = None,
    task: str = "continue",
    selection: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Assemble the exact material later serialized into a provider request."""
    profile_values = normalize_profile(profile)
    project = dict(state.get("project") or {})
    project_id = project_id or str(project.get("id") or "")
    branch_id = branch_id or str(state.get("branch_id") or "main")
    if not project_id:
        raise ValueError("project_id is required")
    chapters = []
    for index, raw in enumerate(state.get("chapters") or [], start=1):
        chapter = dict(raw)
        chapter.setdefault("project_id", project_id)
        chapter.setdefault("branch_id", branch_id)
        chapter["chapter_number"] = index
        chapters.append(chapter)
    by_id = {str(chapter["id"]): chapter for chapter in chapters}
    chapter = by_id.get(str(chapter_id)) if chapter_id else None
    if chapter_id and chapter is None:
        raise ValueError("chapter not found")
    current_number = chapter["chapter_number"] if chapter else len(chapters) + 1
    revision_map = {key: str(value) for key, value in (revisions or {}).items()}
    for item in chapters:
        if item.get("revision_id"):
            revision_map.setdefault(str(item["id"]), str(item["revision_id"]))
    records = [dict(record) for record in state.get("records") or []]
    pov_text = str((chapter or {}).get("pov") or "")
    pov_aliases = resolve_aliases(records, pov_text)
    narration = "omniscient" if pov_text.strip().lower() in {"omniscient", "全知", "全知视角"} else "limited"
    pov_ids = tuple((chapter or {}).get("pov_entity_ids") or pov_aliases["entity_ids"])
    policy = ScopePolicy(project_id, branch_id, current_number, (chapter or {}).get("story_time"), pov_ids, narration)
    historical_policy = ScopePolicy(project_id, branch_id, current_number, (chapter or {}).get("story_time"), pov_ids, narration, include_current=False)
    current_body = str((chapter or {}).get("body") or "")
    chapter_query = " ".join(str((chapter or {}).get(key) or "") for key in ("title", "chapter_goal", "scene_plan", "next_brief", "pov"))
    query = instruction + "\n" + chapter_query + "\n" + current_body[-700:]
    weights = {term: 4.0 for term in lexical_terms(instruction)}
    weights.update({term: max(weights.get(term, 1.0), 2.0) for term in lexical_terms(chapter_query)})
    chunks = [chunk for item in chapters for chunk in chapter_chunks(item, revision_map.get(str(item["id"]), ""))]
    retrieval = search_materials(chunks, query, historical_policy, records=records, chapters=by_id, revisions=revision_map, limit=max(1, profile_values["chapter_top_k"] * 4), query_weights=weights)
    retrieved: list[dict[str, Any]] = []
    used_chapters: set[str] = set()
    material_controls = dict(controls or {})
    chapter_controls = material_controls.get("chapters") or {}
    current_control = chapter_controls.get(str(chapter_id)) if isinstance(chapter_controls, Mapping) and chapter_id else None
    if isinstance(current_control, Mapping) and current_control.get("omit"):
        raise ValueError("the current manuscript cannot be omitted from its writing task")
    if isinstance(chapter_controls, Mapping):
        for pinned_id, control in chapter_controls.items():
            if not isinstance(control, Mapping) or not control.get("pin") or control.get("omit"):
                continue
            if str(pinned_id) == str(chapter_id):
                # The working manuscript already has a mandatory explicit
                # range. Its pin does not turn it into a historical source.
                continue
            if profile_values["chapter_top_k"] == 0:
                raise ValueError("pinned chapters cannot be used with a zero chapter retrieval limit")
            source = by_id.get(str(pinned_id))
            if source is None:
                raise ValueError("pinned chapter does not exist in this work")
            source_body = str(source.get("body") or "")
            pinned_range = control.get("range") or {"start": 0, "end": len(source_body)}
            if not isinstance(pinned_range, Mapping):
                raise ValueError("pinned chapter range is invalid")
            left, right = pinned_range.get("start"), pinned_range.get("end")
            if isinstance(left, bool) or isinstance(right, bool) or not isinstance(left, int) or not isinstance(right, int) or not 0 <= left <= right <= len(source_body):
                raise ValueError("pinned chapter range is invalid")
            if not scope_decision(source, historical_policy, kind="chapter", chapters=by_id, revisions=revision_map)["allowed"]:
                raise ValueError("pinned chapter is outside the current knowledge scope")
            overlapping = [chunk for chunk in chunks if chunk["chapter_id"] == str(pinned_id) and chunk["source_start"] < right and chunk["source_end"] > left]
            if any(not scope_decision(chunk, historical_policy, kind="chapter", chapters=by_id, revisions=revision_map)["allowed"] for chunk in overlapping):
                raise ValueError("pinned chapter passage contains material outside the current viewpoint knowledge")
            pinned = _evidence_item(source, left, right, revision_map.get(str(pinned_id), ""), "pinned")
            pinned["retrieval_reason"] = {"method": "author_pinned_exact_source"}
            retrieved.append(pinned)
            used_chapters.add(str(pinned_id))
        if len(retrieved) > profile_values["chapter_top_k"]:
            raise ValueError("pinned chapter count exceeds the chapter retrieval limit")
    for hit in retrieval["results"]:
        if len(retrieved) >= profile_values["chapter_top_k"]:
            break
        source_id = str(hit["chapter_id"])
        if source_id in used_chapters:
            continue
        used_chapters.add(source_id)
        source = by_id[source_id]
        # Keep the matched paragraph itself. Padding is limited and still
        # checked against private scene annotations through chunk filtering.
        item = _evidence_item(source, hit["source_start"], hit["source_end"], hit["source_revision_id"], "lexical")
        item.update({"relevance": hit["relevance"], "retrieval_reason": hit["retrieval_reason"], "chunk_id": hit["id"]})
        retrieved.append(item)
    excluded = list(retrieval["excluded"])
    previous = []
    for source in chapters[max(0, current_number - 1 - profile_values["recent_window"]):current_number - 1]:
        if str(source["id"]) in used_chapters:
            continue
        source_chunks = chapter_chunks(source, revision_map.get(str(source["id"]), ""))
        for chunk in reversed(source_chunks):
            decision = scope_decision(chunk, historical_policy, kind="chapter", chapters=by_id, revisions=revision_map)
            if decision["allowed"]:
                previous.append(_evidence_item(source, chunk["source_start"], chunk["source_end"], chunk["source_revision_id"], "recency"))
                break
            excluded.append({"id": chunk["id"], "kind": "chapter", "reasons": decision["reasons"]})
    warnings: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    active_terms = set(lexical_terms(query))
    def material_score(item: Mapping[str, Any]) -> float:
        text = " ".join(str(item.get(key) or "") for key in ("name", "title", "note", "description", "summary", "attribute", "value", "source_quote", "payload", "text"))
        matched = active_terms.intersection(lexical_terms(text))
        return sum(weights.get(term, 1.0) for term in matched)
    def filtered(raw: Iterable[Mapping[str, Any]], kind: str, limit: int) -> list[dict[str, Any]]:
        visible = []
        for original in raw:
            item = dict(original)
            item.setdefault("project_id", project_id)
            item.setdefault("branch_id", branch_id)
            decision = scope_decision(item, policy, kind=kind, chapters=by_id, revisions=revision_map)
            if decision["is_plan"] and task in {"outline", "scene", "brainstorm", "blueprint"}:
                planning_policy = ScopePolicy(project_id, branch_id, None, None, (), "omniscient", allow_plans=True)
                if scope_decision(item, planning_policy, kind=kind, chapters=by_id, revisions=revision_map)["allowed"]:
                    plans.append({**item, "epistemic_mode": "author_plan", "use": "planning_only_not_occurred"})
            if not decision["allowed"]:
                excluded.append({"id": item.get("id"), "kind": kind, "reasons": decision["reasons"]})
                continue
            if decision["warnings"]:
                warnings.append({"id": item.get("id"), "kind": kind, "warnings": decision["warnings"]})
            item["epistemic_mode"] = decision["epistemic_mode"]
            visible.append(item)
        if limit <= 0:
            return []
        control_key = {"memory": "memory", "setting": "records"}.get(kind)
        choices = material_controls.get(control_key) if control_key else {}
        choices = choices if isinstance(choices, Mapping) else {}
        pinned_ids = {str(key) for key, value in choices.items() if isinstance(value, Mapping) and value.get("pin") and not value.get("omit")}
        if pinned_ids - {str(item.get("id")) for item in visible}:
            raise ValueError("pinned material is unavailable or outside the current knowledge scope")
        if len(pinned_ids) > limit:
            raise ValueError("pinned material count exceeds its retrieval limit")
        visible.sort(key=lambda item: (str(item.get("id")) not in pinned_ids, -material_score(item)))
        return visible[:limit]
    memory = filtered(state.get("memory") or [], "memory", profile_values["memory_top_k"])
    for item in memory:
        item["retrieval_layer"] = "evidence"
        item["evidence"] = {key: item.get(key) for key in ("source_chapter_id", "source_revision_id", "source_span_id", "source_quote", "source_hash", "source_span_status")}
        item["evidence"]["source_range"] = {"start": item.get("source_start"), "end": item.get("source_end")}
    cards = filtered(records, "setting", profile_values["record_top_k"])
    for item in cards:
        item["retrieval_layer"] = "record"
    story_structure = {}
    for name in ("relationships", "timeline", "foreshadowing", "conflicts", "character_states", "plot_threads"):
        kind = {"relationships": "relationship", "character_states": "character_state"}.get(name, name)
        story_structure[name] = filtered((structure or {}).get(name) or [], kind, 40)
    plot = {}
    for name, items in (state.get("plot") or {}).items():
        plot[name] = filtered([{**item, "epistemic_mode": "author_plan"} for item in items], "plan", 40)
    start, end = 0, len(current_body)
    if selection:
        selected_range = selection.get("range", selection)
        if not isinstance(selected_range, Mapping):
            raise ValueError("selection must contain a source range")
        start, end = selected_range.get("start"), selected_range.get("end")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(current_body):
            raise ValueError("selection source range is invalid")
        if selection.get("revision_id") and selection["revision_id"] != revision_map.get(str(chapter_id), ""):
            raise ValueError("selection revision is stale")
        if selection.get("chapter_hash") and selection["chapter_hash"] != text_hash(current_body):
            raise ValueError("selection manuscript is stale")
    elif task not in _BODY_TASKS:
        start = max(0, len(current_body) - 2400)
    current = None
    if chapter:
        current = {
            "id": chapter["id"], "title": chapter.get("title", ""), "status": chapter.get("status", ""),
            "body": current_body[start:end],
            "source_range": {"start": start, "end": end}, "source_revision_id": revision_map.get(str(chapter_id), ""),
            "source_hash": text_hash(current_body[start:end]), "chapter_hash": text_hash(current_body),
            "full_content": start == 0 and end == len(current_body), "offset_unit": "unicode_codepoint",
            "plan": {key: str(chapter[key]) for key in ("chapter_goal", "scene_plan", "pov", "foreshadowing_op", "hook", "recap", "next_brief") if chapter.get(key)},
        }
    for control_key, limit_key in (("records", "record_top_k"), ("memory", "memory_top_k")):
        choices = material_controls.get(control_key)
        if isinstance(choices, Mapping) and profile_values[limit_key] == 0 and any(isinstance(value, Mapping) and value.get("pin") and not value.get("omit") for value in choices.values()):
            raise ValueError(f"pinned {control_key} cannot be used with a zero retrieval limit")
    context = {
        "project": {key: project.get(key, "") for key in ("id", "title", "genre", "summary", "goal")},
        "confirmed_memory": _controls(memory, material_controls.get("memory")),
        "story_cards": _controls(cards, material_controls.get("records")),
        "previous_chapters": _controls(previous, material_controls.get("chapters")),
        "retrieved_chapters": _controls(retrieved, material_controls.get("chapters")),
        "current_chapter": current, "story_structure": story_structure, "plot": plot,
        "author_plans": _controls(plans[:80], material_controls.get("plans")),
        "material_controls": material_controls,
        "context_policy": "Use cited facts within POV knowledge. Plans have not occurred. Source offsets are Unicode codepoints in the stated revision. Unannotated knowledge is unverified.",
        "context_budget": {
            "profile": profile_values, "retrieval": "paragraph_bm25_cjk+recency+evidence",
            "material_controlled": bool(material_controls), "current_manuscript_fixed": bool(chapter),
            "query_hash": text_hash(query), "author_instruction_used": bool(instruction.strip()),
            "alias_matches": retrieval["aliases"]["entity_ids"], "ambiguous_aliases": retrieval["aliases"]["ambiguous"],
            "knowledge_scope": {
                "pov_entity_ids": list(pov_ids), "narrator_mode": narration,
                "filtered": sorted(excluded, key=lambda item: item.get("kind") == "chapter")[:80],
                "filtered_count": len(excluded), "warnings": warnings[:30],
            },
        },
    }
    return fit_budget(context, profile_values["max_tokens"], reserve_bytes=1800 + len(instruction.encode("utf-8"))), chapter


def _required_string(value: Any, name: str, *, maximum: int = 3000, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be a nonempty string of at most {maximum} characters")
    return value


def validate_source_quote(
    evidence: Mapping[str, Any],
    chapter: Mapping[str, Any],
    revision_id: str,
) -> dict[str, Any]:
    body = str(chapter.get("body") or "")
    quote = _required_string(evidence.get("source_quote"), "source_quote", maximum=3000)
    if evidence.get("source_chapter_id") and evidence["source_chapter_id"] != chapter["id"]:
        raise ValueError("source quote chapter mismatch")
    if evidence.get("source_revision_id") and evidence["source_revision_id"] != revision_id:
        raise ValueError("source quote revision mismatch")
    if evidence.get("chapter_hash") and evidence["chapter_hash"] != text_hash(body):
        raise ValueError("source quote manuscript mismatch")
    left, right = evidence.get("source_start"), evidence.get("source_end")
    if left is None and right is None:
        positions = [match.start() for match in re.finditer(re.escape(quote), body)]
        if len(positions) != 1:
            raise ValueError("source quote is absent or ambiguous; supply an exact range")
        left, right = positions[0], positions[0] + len(quote)
    if isinstance(left, bool) or isinstance(right, bool) or not isinstance(left, int) or not isinstance(right, int) or not 0 <= left < right <= len(body) or body[left:right] != quote:
        raise ValueError("source quote does not match its exact range")
    supplied_hash = evidence.get("source_hash")
    if supplied_hash and supplied_hash != text_hash(quote):
        raise ValueError("source quote hash mismatch")
    return {
        "source_chapter_id": chapter["id"], "source_revision_id": revision_id,
        "source_quote": quote, "source_start": left, "source_end": right,
        "source_start_utf8": len(body[:left].encode("utf-8")), "source_end_utf8": len(body[:right].encode("utf-8")),
        "source_hash": text_hash(quote), "chapter_hash": text_hash(body), "offset_unit": "unicode_codepoint",
    }


EVIDENCE_SCHEMA = {
    "type": "object", "required": ["source_quote"],
    "properties": {"source_quote": {"type": "string"}, "source_start": {"type": "integer"}, "source_end": {"type": "integer"}, "source_chapter_id": {"type": "string"}},
}
CHAPTER_ANALYSIS_SCHEMA = {
    "type": "object", "required": ["summary", "handoff", "candidates"],
    "properties": {
        "summary": {"type": "object", "required": ["text", "evidence"], "properties": {"text": {"type": "string"}, "evidence": {"type": "array", "items": EVIDENCE_SCHEMA}}},
        "handoff": {"type": "object", "required": ["text", "evidence"], "properties": {"text": {"type": "string"}, "evidence": {"type": "array", "items": EVIDENCE_SCHEMA}}},
        "candidates": {
            "type": "array", "items": {
                "type": "object", "required": ["kind", "name", "note", "source_quote", "epistemic_mode", "known_by"],
                "properties": {
                    "kind": {"type": "string", "enum": sorted(CANDIDATE_KINDS)}, "name": {"type": "string"}, "note": {"type": "string"},
                    "source_quote": {"type": "string"}, "source_start": {"type": "integer"}, "source_end": {"type": "integer"},
                    "epistemic_mode": {"type": "string", "enum": sorted(EPISTEMIC_MODES)}, "known_by": {"type": "array", "items": {"type": "string"}},
                    "subject_name": {"type": "string"}, "target_name": {"type": "string"}, "predicate": {"type": "string"},
                    "attribute": {"type": "string"}, "value": {}, "valid_start": {"type": "string"}, "valid_end": {"type": "string"},
                    "story_time": {"type": "string"}, "foreshadowing_stage": {"type": "string", "enum": ["plant", "develop", "payoff"]},
                },
            },
        },
    },
}


def analysis_request(chapter: Mapping[str, Any], *, project_id: str, branch_id: str, revision_id: str) -> dict[str, Any]:
    body = str(chapter.get("body") or "")
    return {
        "task": "chapter_analysis", "response_format": "json", "schema": copy.deepcopy(CHAPTER_ANALYSIS_SCHEMA),
        "source": {"project_id": project_id, "branch_id": branch_id, "chapter_id": chapter["id"], "revision_id": revision_id, "chapter_hash": text_hash(body)},
        "instruction": "Extract an evidence-grounded summary, a next-chapter handoff and candidate facts, events, state changes, relationships and foreshadowing from ordinary manuscript prose. Quote exact source text for every item. Do not invent off-page events, reader knowledge or character knowledge. Distinguish world truth, character belief, inference and future author plans. Empty evidence means no supported finding. Return candidates for author review; do not declare canon or literary quality.",
    }


def validate_chapter_analysis(
    result: Mapping[str, Any],
    chapter: Mapping[str, Any],
    *,
    project_id: str,
    branch_id: str,
    revision_id: str,
    records: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise ValueError("chapter analysis must be an object")
    output: dict[str, Any] = {
        "schema_version": 1, "project_id": project_id, "branch_id": branch_id, "chapter_id": chapter["id"],
        "source_revision_id": revision_id, "chapter_hash": text_hash(str(chapter.get("body") or "")),
        "status": "pending", "review_required": True, "candidates": [],
    }
    for section in ("summary", "handoff"):
        value = result.get(section)
        if not isinstance(value, Mapping):
            raise ValueError(f"{section} must contain text and evidence")
        text = _required_string(value.get("text"), section + ".text", allow_empty=True)
        evidence = value.get("evidence")
        if not isinstance(evidence, list) or len(evidence) > 30 or (text.strip() and not evidence):
            raise ValueError(f"{section} requires exact source evidence")
        output[section] = {"text": text, "evidence": [validate_source_quote(item, chapter, revision_id) for item in evidence if isinstance(item, Mapping)]}
        if len(output[section]["evidence"]) != len(evidence):
            raise ValueError(f"{section} contains invalid evidence")
    raw_candidates = result.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > 120:
        raise ValueError("candidates must be an array of at most 120 items")
    seen: set[str] = set()
    records = list(records)
    record_ids = {str(record.get("id")) for record in records if record.get("id")}
    for raw in raw_candidates:
        if not isinstance(raw, Mapping) or raw.get("kind") not in CANDIDATE_KINDS:
            raise ValueError("candidate kind is invalid")
        name = _required_string(raw.get("name"), "candidate.name", maximum=500)
        note = _required_string(raw.get("note"), "candidate.note", maximum=3000)
        mode = raw.get("epistemic_mode")
        known_by = raw.get("known_by")
        if mode not in EPISTEMIC_MODES or not isinstance(known_by, list) or any(not isinstance(value, str) or not value for value in known_by):
            raise ValueError("candidate must declare epistemic_mode and known_by")
        source = validate_source_quote(raw, chapter, revision_id)
        resolved_known: set[str] = set()
        unresolved_known: list[str] = []
        for value in known_by:
            if value in record_ids or not records:
                resolved_known.add(value)
                continue
            resolution = resolve_aliases(records, value)
            if len(resolution["entity_ids"]) == 1 and not resolution["ambiguous"]:
                resolved_known.add(resolution["entity_ids"][0])
            else:
                unresolved_known.append(value)
        candidate = {
            "kind": raw["kind"], "type": raw["kind"], "name": name, "note": note,
            "epistemic_mode": mode, "known_by": sorted(resolved_known), **source,
            "project_id": project_id, "branch_id": branch_id, "status": "candidate", "review_required": True,
        }
        if unresolved_known:
            candidate["known_by_unresolved"] = sorted(set(unresolved_known))
            candidate["knowledge_scope"] = {"known_by": sorted(resolved_known), "private": True}
            candidate["private"] = True
        for key in ("subject_name", "target_name", "predicate", "attribute", "valid_start", "valid_end", "story_time", "foreshadowing_stage"):
            if key in raw:
                candidate[key] = _required_string(raw[key], "candidate." + key, maximum=500, allow_empty=True)
        if "value" in raw:
            if len(_json(raw["value"])) > 3000:
                raise ValueError("candidate.value too long")
            candidate["value"] = copy.deepcopy(raw["value"])
        if raw["kind"] == "relationship" and (not candidate.get("subject_name") or not candidate.get("target_name") or not candidate.get("predicate")):
            raise ValueError("relationship requires subject_name, target_name and predicate")
        if raw["kind"] == "character_state" and (not candidate.get("subject_name") or not candidate.get("attribute") or "value" not in candidate):
            raise ValueError("character state requires subject_name, attribute and value")
        if raw["kind"] == "foreshadowing" and candidate.get("foreshadowing_stage") not in {"plant", "develop", "payoff"}:
            raise ValueError("foreshadowing requires an evidence stage")
        subject = candidate.get("subject_name") or name
        resolution = resolve_aliases(records, subject)
        candidate["entity_ids"] = resolution["entity_ids"]
        candidate["entity_resolution"] = "ambiguous" if resolution["ambiguous"] else "resolved" if len(resolution["entity_ids"]) == 1 else "unresolved"
        fingerprint = _json({key: candidate.get(key) for key in (
            "project_id", "branch_id", "source_chapter_id", "source_revision_id",
            "source_start", "source_end", "kind", "name", "subject_name", "target_name",
            "predicate", "attribute", "value", "foreshadowing_stage", "epistemic_mode",
        )})
        candidate_id = "analysis_" + text_hash(fingerprint)[:32]
        if candidate_id not in seen:
            seen.add(candidate_id)
            candidate["id"] = candidate_id
            candidate["dedupe_key"] = candidate_id
            output["candidates"].append(candidate)
    output["analysis_key"] = text_hash(_json({"project": project_id, "branch": branch_id, "chapter": chapter["id"], "revision": revision_id, "body": output["chapter_hash"], "schema": 1}))
    return output


def refresh_analysis_status(analysis: Mapping[str, Any], chapter: Mapping[str, Any] | None, revision_id: str) -> dict[str, Any]:
    refreshed = copy.deepcopy(dict(analysis))
    stale = chapter is None or analysis.get("source_revision_id") != revision_id or analysis.get("chapter_hash") != text_hash(str((chapter or {}).get("body") or ""))
    if stale:
        refreshed["status"] = "stale"
        refreshed["review_required"] = True
        for candidate in refreshed.get("candidates", []):
            candidate["status"] = "stale"
            candidate["stale"] = True
        for name in ("summary", "handoff"):
            if isinstance(refreshed.get(name), dict):
                refreshed[name]["stale"] = True
    return refreshed


STORY_REVIEW_SCHEMA = {
    "type": "object", "required": ["findings", "uncertainties"],
    "properties": {
        "findings": {"type": "array", "items": {"type": "object", "required": ["category", "severity", "message", "evidence"], "properties": {
            "category": {"type": "string", "enum": sorted(REVIEW_CATEGORIES)}, "severity": {"type": "string", "enum": ["suggestion", "possible", "contradiction"]},
            "message": {"type": "string"}, "suggestion": {"type": "string"}, "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
        }}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
}


def sent_evidence_ranges(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Enumerate only exact prose that the serialized model context contains."""
    if not isinstance(context, Mapping):
        raise ValueError("sent context must be an object")
    allowed = []
    def add(item: Mapping[str, Any], *, current: bool = False):
        evidence = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
        chapter_id = str(item.get("source_chapter_id") or evidence.get("source_chapter_id") or item.get("id") or "")
        revision = str(item.get("source_revision_id") or evidence.get("source_revision_id") or "")
        source_range = item.get("source_range") or evidence.get("source_range")
        if not isinstance(source_range, Mapping):
            source_range = {"start": item.get("source_start"), "end": item.get("source_end")}
        quote = item.get("body") if current else item.get("excerpt", item.get("source_quote", evidence.get("source_quote")))
        start, end = source_range.get("start"), source_range.get("end")
        if chapter_id and revision and isinstance(quote, str) and isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and not isinstance(end, bool) and 0 <= start <= end and len(quote) == end - start:
            allowed.append({
                "source_chapter_id": chapter_id, "source_revision_id": revision,
                "source_start": start, "source_end": end, "source_quote": quote,
            })
    current = context.get("current_chapter")
    if isinstance(current, Mapping):
        add(current, current=True)
    for collection in ("retrieved_chapters", "previous_chapters", "confirmed_memory"):
        for item in context.get(collection) or []:
            if isinstance(item, Mapping):
                add(item)
    for collection in (context.get("story_structure") or {}).values():
        for item in collection:
            if isinstance(item, Mapping) and item.get("source_quote"):
                add(item)
    return allowed


def validate_story_review(
    result: Mapping[str, Any],
    chapters: Mapping[str, Mapping[str, Any]],
    revisions: Mapping[str, str],
    *,
    project_id: str,
    branch_id: str,
    current_chapter_id: str,
    allowed_evidence: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(result, Mapping) or not isinstance(result.get("findings"), list) or len(result["findings"]) > 80:
        raise ValueError("review findings must be an array of at most 80 items")
    uncertainties = result.get("uncertainties")
    if not isinstance(uncertainties, list) or any(not isinstance(item, str) or len(item) > 2000 for item in uncertainties):
        raise ValueError("review uncertainties must be strings")
    allowed = list(allowed_evidence) if allowed_evidence is not None else None
    findings = []
    for raw in result["findings"]:
        if not isinstance(raw, Mapping) or raw.get("category") not in REVIEW_CATEGORIES or raw.get("severity") not in {"suggestion", "possible", "contradiction"}:
            raise ValueError("review category or severity is invalid")
        message = _required_string(raw.get("message"), "review.message")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12:
            raise ValueError("every review finding requires source evidence")
        quotes = []
        for item in evidence:
            if not isinstance(item, Mapping):
                raise ValueError("review evidence must be an object")
            chapter_id = str(item.get("source_chapter_id") or current_chapter_id)
            chapter = chapters.get(chapter_id)
            if chapter is None or str(chapter.get("project_id") or project_id) != project_id or str(chapter.get("branch_id") or branch_id) != branch_id:
                raise ValueError("review evidence is outside the current work")
            quote = validate_source_quote(item, chapter, revisions.get(chapter_id, ""))
            if allowed is not None and not any(
                span.get("source_chapter_id") == chapter_id
                and span.get("source_revision_id") == quote["source_revision_id"]
                and span.get("source_start", -1) <= quote["source_start"]
                and span.get("source_end", -1) >= quote["source_end"]
                and str(span.get("source_quote") or "")[
                    quote["source_start"] - span["source_start"]:quote["source_end"] - span["source_start"]
                ] == quote["source_quote"]
                for span in allowed
            ):
                raise ValueError("review evidence was not included in the material sent to the model")
            quotes.append(quote)
        if raw["severity"] == "contradiction" and len({(quote["source_chapter_id"], quote["source_start"], quote["source_end"]) for quote in quotes}) < 2:
            raise ValueError("a contradiction requires both distinct source passages")
        finding = {
            "category": raw["category"], "severity": raw["severity"], "message": message,
            "suggestion": _required_string(raw.get("suggestion", ""), "review.suggestion", allow_empty=True),
            "evidence": quotes, "status": "pending", "review_required": True,
            "source": "model_suggestion", "literary_quality_verified": False,
        }
        finding["id"] = "review_" + text_hash(_json(finding))[:32]
        findings.append(finding)
    return {
        "project_id": project_id, "branch_id": branch_id, "chapter_id": current_chapter_id,
        "revision_id": revisions.get(current_chapter_id, ""), "status": "pending_author_review",
        "findings": findings, "uncertainties": uncertainties,
        "evidence_scope_verified": allowed is not None,
        "literary_quality_verified": False, "auto_pass": False,
    }
