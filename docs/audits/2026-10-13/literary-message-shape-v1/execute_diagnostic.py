"""Frozen one-variable message-shape diagnosis; never retry a reserved request."""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
import moyu_provider as provider
from scripts import literary_evaluation as evaluation

OUT = Path(__file__).resolve().parent
AUDITS = OUT.parent
REVISION = AUDITS / "literary-revision-v1"
TRANSPORT = AUDITS / "literary-transport-v1"
PROTOCOL = "moyu-message-shape-diagnosis-v1"
SEPARATOR = "\n\n"
PARAMETERS = {"temperature": 0.65, "thinking": {"type": "disabled"}}
CONFIG = {"base_url": "https://api.icodexs.com/v1", "model": "glm-5.1",
          "total_timeout": 180, "api_key": ""}
PRIOR = {"requests_reserved": 34, "complete_tokens": 63876,
         "known_lower_bound": 63876, "missing_complete_usage": 10}
HISTORICAL_FILES = {
    "literary-revision-v1/frozen-writer-requests.json":
        "0b155b870a4e1f58170037825a56b051a7c107dcb08cb1c83263667d27a80f87",
    "literary-revision-v1/plan.json":
        "f229e5ac523783621732d2a78e3d6c0e6229a6b64a1dc8dbf3f4c4979802953a",
    "literary-revision-v1/parent-provenance.json":
        "236ea7333c613ced09c73fbb6aa57955774c4c04b24d3974029a85382e581b68",
    "literary-revision-v1/samples/mystery-tide--1--moyu.json":
        "8fb372259ab09d5733a124f37a43c78e28f8927156089feb0180e7a7385316a4",
    "literary-transport-v1/frozen-requests.json":
        "2b2add93409fabd062d93b9bb86a9786b6ab7abd54361a33dd6fc4d6efd97f7d",
    "literary-transport-v1/samples/nonstream.json":
        "cee342d295c726731d6a9de7ac087fd0a148cae770fc4e22c3c21554aa044117",
    "literary-transport-v1/stage-one-review.json":
        "50ba2d9bc414fef898f7483292be4d950fe96b16fb432f2497ce37b05755be5a",
}
UNMERGED_BODY_SHA = "729c3fe156b6a98444e9ecb167a2352d01efceb05973bab7ef33603a05a0559c"
FACTUAL_CHECKS = (
    "confirmed_facts_preserved",
    "original_manuscript_overrides_candidate_conflicts",
    "limited_lin_lan_pov",
    "departure_before_22_10",
    "old_pier_and_left_boot_key_drive_choice",
    "complete_causal_scene_without_analysis",
)
SOURCE_FILES = ("moyu_provider.py", "moyu_output.py",
                "scripts/literary_evaluation.py")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def verify_history() -> None:
    for name, expected in HISTORICAL_FILES.items():
        if digest(AUDITS / name) != expected:
            raise ValueError("历史证据指纹变化，已停止；没有发送请求。")


def merge_adjacent_users(messages: list[dict]) -> list[dict]:
    """Merge plain adjacent user contents, preserving each original substring."""
    merged = []
    for message in messages:
        if (not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or not isinstance(message["content"], str)):
            raise ValueError("待合并消息不是冻结的纯文字形状。")
        if merged and message["role"] == "user" and merged[-1]["role"] == "user":
            merged[-1]["content"] += SEPARATOR + message["content"]
        else:
            merged.append(copy.deepcopy(message))
    return merged


def inputs() -> dict:
    verify_history()
    original = evaluation.read_json(TRANSPORT / "frozen-requests.json")["false"]
    payload = json.loads(original["body_utf8"])
    if (payload != original["payload"]
            or digest_bytes(original["body_utf8"].encode()) != UNMERGED_BODY_SHA):
        raise ValueError("历史非流式请求正文与记录不一致。")
    revision = evaluation.read_json(
        REVISION / "frozen-writer-requests.json")["mystery-tide--moyu"]
    messages = payload["messages"]
    if (messages != revision["payload"]["messages"]
            or [item["role"] for item in messages] != ["system", "user", "user"]):
        raise ValueError("原整理消息不再是冻结的三条消息。")
    prepared_original = provider.prepare_chat_request(
        CONFIG, messages, task="continue", max_tokens=3000, stream=False,
        parameters=PARAMETERS)
    if prepared_original["body"].decode("utf-8") != original["body_utf8"]:
        raise ValueError("产品预览改变了历史非流式请求，已停止。")

    changed = merge_adjacent_users(messages)
    prepared = provider.prepare_chat_request(
        CONFIG, changed, task="continue", max_tokens=3000, stream=False,
        parameters=PARAMETERS)
    if ({key: value for key, value in prepared["payload"].items() if key != "messages"}
            != {key: value for key, value in payload.items() if key != "messages"}):
        raise ValueError("出现消息合并以外的参数变化。")
    system, material, candidate = (item["content"].encode() for item in messages)
    merged_user = changed[1]["content"].encode()
    candidate_data = json.loads(messages[-1]["content"])
    parent = evaluation.read_json(
        AUDITS / "literary-pilot-v12/samples/mystery-tide--1--moyu.json")
    candidate_text = candidate_data.get("candidate_text", "")
    if (changed[0] != messages[0]
            or merged_user != material + SEPARATOR.encode() + candidate
            or not merged_user.endswith(candidate)
            or candidate_data.get("candidate_adopted") is not False
            or candidate_data.get("author_notes") != ""
            or candidate_data.get("actual_characters") != 868
            or candidate_text != parent["result"]["text"]
            or evaluation.count_characters(candidate_text) != 868):
        raise ValueError("系统、资料或完整原候选发生变化。")

    before = encoded(payload["messages"])
    after = encoded(prepared["payload"]["messages"])
    body = prepared["body"]
    return {
        "body_utf8": body.decode("utf-8"),
        "payload": prepared["payload"],
        "endpoint": prepared["endpoint"],
        "request_sha256": digest_bytes(body),
        "messages_sha256": digest_bytes(after),
        "task": "continue",
        "difference": {
            "only_variable": "merge_adjacent_user_messages",
            "separator_utf8_hex": SEPARATOR.encode().hex(),
            "separator_utf8_bytes": len(SEPARATOR.encode()),
            "separator_json_string_bytes": len(encoded(SEPARATOR)) - 2,
            "messages_before": len(messages),
            "messages_after": len(changed),
            "unmerged_body_sha256": UNMERGED_BODY_SHA,
            "body_bytes_before": len(prepared_original["body"]),
            "body_bytes_after": len(body),
            "body_bytes_delta": len(body) - len(prepared_original["body"]),
            "messages_bytes_before": len(before),
            "messages_bytes_after": len(after),
            "messages_bytes_delta": len(after) - len(before),
            "removed_message_boundary_json_bytes": len(before) - len(after)
                + len(encoded(SEPARATOR)) - 2,
            "content_bytes_before": sum(len(item["content"].encode()) for item in messages),
            "content_bytes_after": sum(len(item["content"].encode()) for item in changed),
            "content_bytes_delta": len(SEPARATOR.encode()),
            "system_utf8_sha256": digest_bytes(system),
            "system_utf8_bytes": len(system),
            "original_material_utf8_sha256": digest_bytes(material),
            "original_material_utf8_bytes": len(material),
            "candidate_json_utf8_sha256": digest_bytes(candidate),
            "candidate_json_utf8_bytes": len(candidate),
            "candidate_json_start_byte_in_merged_user": len(material) + len(SEPARATOR.encode()),
            "candidate_json_is_identical_complete_suffix": True,
            "candidate_text_sha256": digest_bytes(candidate_text.encode()),
            "candidate_characters": 868,
            "system_is_identical": True,
            "non_message_parameters_are_identical": True,
        },
    }


def history_paths(out: Path) -> list[Path]:
    paths = [path for path in AUDITS.glob("literary-*/request-ledger.json")
             if path.parent.resolve() != out.resolve()]
    paths.extend(AUDITS.glob("provider-shape-*.json"))
    return sorted(paths)


def history_snapshot(out: Path) -> dict:
    return {str(path.relative_to(AUDITS)): digest(path) for path in history_paths(out)}


def cumulative(out: Path = OUT) -> dict:
    rows = []
    for path in history_paths(out):
        data = evaluation.read_json(path)
        if path.name == "request-ledger.json":
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise ValueError("历史账本形状无效，停止执行。")
            rows.extend(data)
        else:
            usage = next((event["usage"] for event in reversed(data.get("events", []))
                          if isinstance(event, dict)
                          and isinstance(event.get("usage"), dict)), None)
            rows.append({"usage": usage, "usage_accounting": data.get("usage_accounting")})
    own_ledger = out / "request-ledger.json"
    if own_ledger.exists():
        rows.extend(evaluation.read_json(own_ledger))
    accounting = [provider.usage_accounting(row.get("usage"), row.get("usage_accounting"))
                  for row in rows]
    return {
        "requests_reserved": len(rows),
        "complete_tokens": sum(item["total_tokens"] or 0 for item in accounting),
        "known_lower_bound": sum(item["reported_tokens_lower_bound"] for item in accounting),
        "missing_complete_usage": sum(item["state"] != "reported" for item in accounting),
        "request_cap": 60,
        "known_tokens_stop_threshold": 180000,
        "currency_cost": "unknown",
    }


def source_snapshot() -> dict:
    files = {name: digest(ROOT / name) for name in SOURCE_FILES}
    files["execution_source"] = digest(Path(__file__))
    files["offline_fixtures"] = digest(OUT / "offline_fixtures.py")
    files["preregistered_protocol"] = digest(OUT / "preregistered-protocol.md")
    return files


class Observer:
    """Count every decoded event from this one response, without reasoning text."""
    def __init__(self) -> None:
        self.body: list[str] = []
        self.stats = {
            "events": 0, "content_pieces": 0, "content_characters": 0,
            "reasoning_field_events": 0, "reasoning_nonempty_events": 0,
            "reasoning_invalid_events": 0,
            "reasoning_characters": 0, "reasoning_first_event": None,
            "reasoning_last_event": None, "refusal_nonempty_events": 0,
            "unexplained_shapes": 0, "finish_reasons": [], "models": [],
            "raw_numeric_usage": [], "usage_shapes": [], "http_responses": 0,
            "http_status": None, "content_type": None,
            "modes_observed": [], "events_recorded": 0,
        }
        self.shapes: list[dict] = []

    @staticmethod
    def shape(value: object) -> dict:
        if value is None:
            return {"type": "null"}
        if isinstance(value, str):
            return {"type": "string", "characters": len(value)}
        if isinstance(value, dict):
            return {"type": "object", "fields": len(value)}
        if isinstance(value, list):
            return {"type": "array", "items": len(value)}
        return {"type": type(value).__name__}

    @staticmethod
    def numeric_usage(value: object) -> dict | None:
        if not isinstance(value, dict):
            return None
        allowed = {"prompt_tokens", "completion_tokens", "total_tokens",
                   "prompt_tokens_details", "completion_tokens_details"}
        if set(value) - allowed:
            return None
        for item in value.values():
            values = item.values() if isinstance(item, dict) else [item]
            if any(part is not None and (
                    isinstance(part, bool) or not isinstance(part, int) or part < 0)
                   for part in values):
                return None
        return copy.deepcopy(value)

    def response(self, response: object) -> None:
        self.stats["http_responses"] += 1
        self.stats["http_status"] = response.status
        self.stats["content_type"] = (
            response.getheader("Content-Type", "").partition(";")[0].strip().lower())

    def observe(self, data: object, sse: bool) -> None:
        stats = self.stats
        stats["events"] += 1
        sequence = stats["events"]
        mode = "sse" if sse else "json"
        if mode not in stats["modes_observed"]:
            stats["modes_observed"].append(mode)
        shape = {"event": sequence, "mode": mode, **self.shape(data)}
        # Keep a bounded shape log while counters above remain complete.
        if len(self.shapes) == 128:
            del self.shapes[112]
        self.shapes.append(shape)
        stats["events_recorded"] = len(self.shapes)
        if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
            stats["unexplained_shapes"] += 1
            return
        allowed_top = {"id", "object", "created", "model", "choices",
                       "usage", "system_fingerprint", "service_tier"}
        if set(data) - allowed_top:
            stats["unexplained_shapes"] += 1
        model = data.get("model")
        if isinstance(model, str) and model:
            if model not in stats["models"]:
                stats["models"].append(model)
        elif model is not None:
            stats["unexplained_shapes"] += 1
        if data.get("usage") is not None:
            shape["usage"] = self.shape(data["usage"])
            stats["usage_shapes"].append(self.shape(data["usage"]))
            usage = self.numeric_usage(data["usage"])
            if usage is None:
                stats["unexplained_shapes"] += 1
            else:
                stats["raw_numeric_usage"].append(usage)
        choices = data["choices"]
        if len(choices) > 1 or not sse and len(choices) != 1:
            stats["unexplained_shapes"] += 1
        shape["choices"] = []
        for choice in choices:
            choice_shape = self.shape(choice)
            shape["choices"].append(choice_shape)
            if not isinstance(choice, dict):
                stats["unexplained_shapes"] += 1
                continue
            if set(choice) - {"index", "message", "delta", "finish_reason", "logprobs"}:
                stats["unexplained_shapes"] += 1
            if choice.get("index", 0) != 0:
                stats["unexplained_shapes"] += 1
            finish = choice.get("finish_reason")
            choice_shape["finish_reason"] = self.shape(finish)
            if finish not in (None, ""):
                if isinstance(finish, str):
                    stats["finish_reasons"].append(finish)
                else:
                    stats["unexplained_shapes"] += 1
            message = choice.get("delta" if sse else "message")
            choice_shape["message"] = self.shape(message)
            if not isinstance(message, dict):
                stats["unexplained_shapes"] += 1
                continue
            fields = {name: self.shape(message[name]) for name in
                      ("role", "content", "reasoning_content", "reasoning", "refusal")
                      if name in message}
            choice_shape["fields"] = fields
            if set(message) - {"role", "content", "reasoning_content", "reasoning", "refusal"}:
                stats["unexplained_shapes"] += 1
            if "role" in message and message["role"] != "assistant":
                stats["unexplained_shapes"] += 1
            for name in ("reasoning_content", "reasoning"):
                if name not in message:
                    continue
                stats["reasoning_field_events"] += 1
                if stats["reasoning_first_event"] is None:
                    stats["reasoning_first_event"] = sequence
                stats["reasoning_last_event"] = sequence
                value = message[name]
                if isinstance(value, str):
                    stats["reasoning_characters"] += len(value)
                    stats["reasoning_nonempty_events"] += bool(value.strip())
                elif value is not None:
                    stats["reasoning_invalid_events"] += 1
                    stats["unexplained_shapes"] += 1
            refusal = message.get("refusal")
            if isinstance(refusal, str):
                stats["refusal_nonempty_events"] += bool(refusal.strip())
            elif refusal is not None:
                stats["unexplained_shapes"] += 1
            content = message.get("content")
            if isinstance(content, str):
                self.body.append(content)
                stats["content_pieces"] += 1
                stats["content_characters"] += len(content)
            elif content is not None:
                stats["unexplained_shapes"] += 1

    def saved_stats(self, result: dict, secret: str) -> dict:
        independent = "".join(self.body)
        stats = {**self.stats, "body_sha256": digest_bytes(independent.encode()),
                 "body_matches_product": independent == result.get("text"),
                 "events_truncated": self.stats["events"] > len(self.shapes),
                 "shape_events": self.shapes}
        return provider._redact(stats, secret)


@contextlib.contextmanager
def observing(observer: Observer):
    original_payload = provider._Accumulator.payload
    original_response = http.client.HTTPConnection.getresponse

    def observed_payload(accumulator, data, *, sse, **options):
        observer.observe(data, sse)
        return original_payload(accumulator, data, sse=sse, **options)

    def observed_response(connection):
        response = original_response(connection)
        observer.response(response)
        return response

    provider._Accumulator.payload = observed_payload
    http.client.HTTPConnection.getresponse = observed_response
    try:
        yield
    finally:
        provider._Accumulator.payload = original_payload
        http.client.HTTPConnection.getresponse = original_response


def mechanical_checks(record: dict, observer: dict, expected: dict) -> tuple[dict, dict]:
    result = record.get("result") or {}
    case = evaluation.read_json(REVISION / "plan.json")["cases"][0]
    validation = evaluation.sample_validation(case, record)
    accounting = provider.usage_accounting(result.get("usage"), result.get("usage_accounting"))
    raw_usage = observer.get("raw_numeric_usage", [])
    summary = result.get("reasoning_observation")
    summary_matches = (
        isinstance(summary, dict)
        and summary.get("field_events") == observer["reasoning_field_events"]
        and summary.get("nonempty_events") == observer["reasoning_nonempty_events"]
        and summary.get("invalid_events") == observer["reasoning_invalid_events"]
        and summary.get("characters") == observer["reasoning_characters"]
        and summary.get("requested_disabled") is True
        and summary.get("unexpected_reasoning") is
        (observer["reasoning_nonempty_events"] > 0
         or observer["reasoning_invalid_events"] > 0))
    checks = {
        "status_complete": record.get("status") == "complete",
        "http_once_and_success": observer["http_responses"] == 1 and observer["http_status"] == 200,
        "nonstream_json_mode": observer["content_type"] == "application/json"
            and observer["modes_observed"] == ["json"] and observer["events"] == 1,
        "exact_model_observed": observer["models"] == ["glm-5.1"]
            and result.get("returned_model") == "glm-5.1",
        "actual_stop_complete": observer["finish_reasons"] == ["stop"]
            and result.get("finish_reason") == "stop"
            and result.get("completion_state") == "complete"
            and result.get("safe_to_apply") is True
            and result.get("truncated") is False,
        "all_shapes_explained": observer["unexplained_shapes"] == 0,
        "no_nonempty_reasoning": observer["reasoning_nonempty_events"] == 0,
        "no_invalid_reasoning_shapes": observer["reasoning_invalid_events"] == 0,
        "reasoning_summary_matches": summary_matches,
        "no_refusal": observer["refusal_nonempty_events"] == 0,
        "independent_body_matches": observer["body_matches_product"],
        "complete_consistent_usage": accounting["state"] == "reported"
            and len(raw_usage) == 1
            and raw_usage[0] == result.get("usage")
            and provider.usage_accounting(raw_usage[0])["state"] == "reported",
        "same_frozen_request": result.get("request_audit", {}).get("request_sha256")
            == expected["request_sha256"],
        "protocol_and_output_eligible": validation["eligible_for_blind_review"] is True,
    }
    return checks, validation


def self_check() -> None:
    original = [{"role": "system", "content": "系统保持"},
                {"role": "user", "content": "原资料"},
                {"role": "user", "content": '{"candidate_text":"完整候选"}'}]
    changed = merge_adjacent_users(original)
    assert changed[0] == original[0] and len(changed) == 2
    assert changed[1]["content"] == "原资料" + SEPARATOR + original[2]["content"]
    observer = Observer()
    observer.observe({"model": "fixture", "choices": [{"message": {
        "content": "甲", "reasoning_content": "  "}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}, False)
    observer.observe({"choices": [{"delta": {"content": "乙",
        "reasoning_content": "私密推理"}, "finish_reason": None}]}, True)
    assert "".join(observer.body) == "甲乙"
    assert observer.stats["reasoning_field_events"] == 2
    assert observer.stats["reasoning_nonempty_events"] == 1
    assert observer.stats["reasoning_characters"] == 6
    assert "私密推理" not in json.dumps(observer.saved_stats({"text": "甲乙"}, ""))
    assert provider.usage_accounting(None)["state"] != "reported"
    assert provider.usage_accounting({"prompt_tokens": 2, "completion_tokens": 3,
                                     "total_tokens": 6})["state"] != "reported"


def preview(out: Path = OUT) -> dict:
    self_check()
    frozen = inputs()
    prior = cumulative(out)
    if any(prior[name] != expected for name, expected in PRIOR.items()):
        raise ValueError("累计账本起点变化；不能继续沿用本方案或换目录归零。")
    return {"protocol": PROTOCOL, "sent_requests": 0, "prepared": False,
            "request_sha256": frozen["request_sha256"],
            "messages_sha256": frozen["messages_sha256"],
            "difference": frozen["difference"], "cumulative_before": prior,
            "source_sha256_preview": source_snapshot(),
            "historical_ledger_sha256": history_snapshot(out)}


def prepare(out: Path = OUT) -> dict:
    protected = ("plan.json", "frozen-request.json", "frozen-request.body.json",
                 "preparation-evidence.json", "request-ledger.json")
    if any((out / name).exists() for name in protected):
        raise ValueError("此诊断已经冻结或预留，禁止覆盖和重发。")
    evidence = preview(out)
    frozen = inputs()
    plan = {
        "protocol": PROTOCOL, "run_id": uuid.uuid4().hex, "created_at": evaluation.now(),
        "single_variable": "merge_adjacent_user_messages",
        "first_stage": "merged_nonstream",
        "second_stage": "same_body_nonstream_stability_only_after_all_gates_pass",
        "new_requests_cap": 2,
        "budget": {"max_requests": 2, "reported_tokens_stop_threshold":
                   180000 - PRIOR["known_lower_bound"], "consecutive_unknown_stop_threshold": 1},
        "cumulative_before": evidence["cumulative_before"],
        "source_sha256": evidence["source_sha256_preview"],
        "historical_ledger_sha256": evidence["historical_ledger_sha256"],
        "historical_evidence_sha256": HISTORICAL_FILES,
        "request_sha256": frozen["request_sha256"],
        "messages_sha256": frozen["messages_sha256"],
        "difference": frozen["difference"],
        "independent_stories_added": 0, "blind_grades": 0,
        "factual_checks": list(FACTUAL_CHECKS),
    }
    evaluation.write_json(out / "frozen-request.json", frozen)
    (out / "frozen-request.body.json").write_bytes(frozen["body_utf8"].encode())
    plan["frozen_request_file_sha256"] = digest(out / "frozen-request.json")
    plan["frozen_body_file_sha256"] = digest(out / "frozen-request.body.json")
    evidence.update(prepared=True, run_id=plan["run_id"])
    evaluation.write_json(out / "preparation-evidence.json", evidence)
    plan["preparation_evidence_file_sha256"] = digest(out / "preparation-evidence.json")
    evaluation.write_json(out / "plan.json", plan)
    return {"prepared": True, "sent_requests": 0, "run_id": plan["run_id"],
            "request_sha256": frozen["request_sha256"],
            "cumulative_before": evidence["cumulative_before"]}


def verified_plan(out: Path) -> tuple[dict, dict]:
    plan = evaluation.read_json(out / "plan.json")
    evaluation.run_identity(plan)
    if (plan.get("protocol") != PROTOCOL
            or plan.get("source_sha256") != source_snapshot()
            or plan.get("historical_evidence_sha256") != HISTORICAL_FILES
            or plan.get("historical_ledger_sha256") != history_snapshot(out)):
        raise ValueError("执行来源或历史账本变化，已停止；没有发送请求。")
    for name, key in (("frozen-request.json", "frozen_request_file_sha256"),
                      ("frozen-request.body.json", "frozen_body_file_sha256"),
                      ("preparation-evidence.json", "preparation_evidence_file_sha256")):
        if digest(out / name) != plan.get(key):
            raise ValueError("冻结文件指纹变化，已停止；没有发送请求。")
    frozen = inputs()
    if (frozen != evaluation.read_json(out / "frozen-request.json")
            or frozen["body_utf8"].encode() != (out / "frozen-request.body.json").read_bytes()
            or plan.get("request_sha256") != frozen["request_sha256"]
            or plan.get("messages_sha256") != frozen["messages_sha256"]
            or plan.get("difference") != frozen["difference"]
            or plan.get("budget", {}).get("max_requests") != 2
            or plan.get("factual_checks") != list(FACTUAL_CHECKS)):
        raise ValueError("诊断方案或冻结请求变化，已停止；没有发送请求。")
    return plan, frozen


def stage_identity(plan: dict, stage: str) -> str:
    return "diag_" + evaluation.sha({"run_id": plan["run_id"], "stage": stage})[:32]


def verified_first_stage(out: Path, plan: dict, frozen: dict) -> dict:
    path = out / "samples/merged_nonstream.json"
    record = evaluation.read_json(path)
    review = evaluation.read_json(out / "stage-one-review.json")
    expected_identity = stage_identity(plan, "merged_nonstream")
    audits = [record.get("request_audit"), record.get("result", {}).get("request_audit")]
    if (review.get("protocol") != PROTOCOL or review.get("all_gates_passed") is not True
            or review.get("record_sha256") != digest(path)
            or review.get("request_id") != expected_identity
            or record.get("mechanical_gates_passed") is not True
            or record.get("mechanical_checks") != review.get("mechanical_checks")
            or record.get("request_identity") != expected_identity
            or any(not isinstance(audit, dict)
                   or audit.get("request_id") != expected_identity
                   or audit.get("request_sha256") != frozen["request_sha256"] for audit in audits)):
        raise ValueError("第一阶段未通过全部机械和事实检查，第二阶段已停止。")
    validate_factual_review(review.get("factual_review"), record)
    return record


def execute(stage: str, out: Path = OUT) -> dict:
    if stage not in {"merged_nonstream", "stability_nonstream"}:
        raise ValueError("诊断阶段无效。")
    with evaluation.execution_lock(out):
        plan, frozen = verified_plan(out)
        path = out / "samples" / (stage + ".json")
        rows = evaluation.read_json(out / "request-ledger.json") if (
            out / "request-ledger.json").exists() else []
        if path.exists() or any(row.get("request_id") == stage for row in rows):
            raise ValueError("本阶段已预留，禁止重发可能已经收费的请求。")
        if stage == "merged_nonstream" and rows:
            raise ValueError("第一阶段应从空的新账本开始。")
        first = verified_first_stage(out, plan, frozen) if stage == "stability_nonstream" else None
        if first is not None and (
                len(rows) != 1 or rows[0].get("request_id") != "merged_nonstream"
                or rows[0].get("status") != "complete"):
            raise ValueError("第一阶段账本状态不完整，第二阶段已停止。")
        prior = cumulative(out)
        expected_count = PRIOR["requests_reserved"] + (1 if first else 0)
        if (prior["requests_reserved"] != expected_count
                or prior["missing_complete_usage"] != PRIOR["missing_complete_usage"]
                or prior["requests_reserved"] >= 60 or prior["known_lower_bound"] >= 180000):
            raise ValueError("累计预算或缺用量状态变化，已停止；没有发送请求。")
        key = os.environ.get("MOYU_AI_API_KEY", "")
        if not key:
            raise ValueError("运行环境尚无密钥，未预留也未发送请求。")
        config = {**CONFIG, "api_key": key}
        request_id = stage_identity(plan, stage)
        if first and request_id == first["request_identity"]:
            raise ValueError("稳定性阶段缺少独立请求身份。")
        prepared = provider.prepare_chat_request(
            config, frozen["payload"]["messages"], task="continue", max_tokens=3000,
            stream=False, parameters=PARAMETERS, request_id=request_id)
        if prepared["body"] != frozen["body_utf8"].encode():
            raise ValueError("实际请求与冻结正文不同，已停止。")
        ledger = evaluation.Ledger(out, plan)
        index = ledger.reserve(stage, "writer", "glm-5.1")
        record = {
            "id": stage, "protocol": PROTOCOL, "status": "reserved",
            "started_at": evaluation.now(), "request_identity": request_id,
            "request_audit": prepared["request_audit"], "automatic_retry": False,
            "frozen_request_sha256": frozen["request_sha256"],
            "independent_stories_added": 0, "blind_grades": 0,
        }
        evaluation.write_json(path, record)
        observer = Observer()
        dispatch_count = 0

        def dispatch(audit: dict) -> None:
            nonlocal dispatch_count
            dispatch_count += 1
            if (dispatch_count != 1 or audit.get("request_id") != request_id
                    or audit.get("request_sha256") != frozen["request_sha256"]):
                raise ValueError("外部发送身份、正文或次数与预注册不一致。")
            ledger.mark_dispatch(index, audit)
            record["request_audit"] = audit
            evaluation.write_json(path, record)

        with observing(observer):
            try:
                result = provider.call_chat_completion(
                    config, frozen["payload"]["messages"], task="continue", max_tokens=3000,
                    stream=False, parameters=PARAMETERS, request_id=request_id, on_dispatch=dispatch)
                record.update(status="complete", result=result)
            except provider.ProviderError as error:
                record.update(status=error.status, error_code=error.code, result=error.partial_result)
            except BaseException as error:
                partial = getattr(error, "partial_result", None) or {
                    "request_audit": record["request_audit"], "billing_state": "unknown",
                    "completion_state": "unknown", "safe_to_apply": False,
                }
                record.update(status="unknown", error_type=type(error).__name__, result=partial)
        result = record.get("result") or {
            "request_audit": record["request_audit"], "completion_state": "unknown",
            "billing_state": "unknown", "safe_to_apply": False}
        record["result"] = provider._redact(result, key)
        stats = observer.saved_stats(record["result"], key)
        checks, validation = mechanical_checks(record, stats, frozen)
        checks["one_dispatch_observed"] = dispatch_count == 1
        record.update(
            ended_at=evaluation.now(), observer=stats, output_validation=validation,
            mechanical_checks=checks, mechanical_gates_passed=all(checks.values()),
            factual_review="pending" if all(checks.values()) else "not_eligible",
            stopped_after_failure=not all(checks.values()))
        ledger.finish(index, record["result"], record["status"])
        record["cumulative_after"] = cumulative(out)
        evaluation.write_json(path, record)
        path.with_suffix(".md").write_text(record["result"].get("text", ""), encoding="utf-8")
        return {"stage": stage, "status": record["status"],
                "characters": validation["characters"], "checks": checks,
                "cumulative_after": record["cumulative_after"],
                "next_stage_eligible": False, "factual_review_required": all(checks.values())}


def validate_factual_review(review: object, record: dict) -> bool:
    if (not isinstance(review, dict) or review.get("record_id") != record.get("id")
            or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
            or review.get("text_sha256") != digest_bytes(record["result"]["text"].encode())
            or not isinstance(review.get("checks"), dict)
            or set(review["checks"]) != set(FACTUAL_CHECKS)):
        raise ValueError("事实核查必须对应完整原文，并包含全部冻结检查。")
    text = record["result"]["text"]
    for name in FACTUAL_CHECKS:
        item = review["checks"][name]
        if (not isinstance(item, dict) or type(item.get("passed")) is not bool
                or not isinstance(item.get("rationale"), str) or not item["rationale"].strip()
                or not isinstance(item.get("evidence"), list) or not item["evidence"]):
            raise ValueError("每项事实核查都需结论、原因和正文依据。")
        for evidence in item["evidence"]:
            if (not isinstance(evidence, dict) or not isinstance(evidence.get("quote"), str)
                    or not evidence["quote"].strip() or evidence["quote"] not in text
                    or not isinstance(evidence.get("note"), str) or not evidence["note"].strip()):
                raise ValueError("事实依据必须逐字来自这份候选，不能使用别轮候选。")
    return all(review["checks"][name]["passed"] for name in FACTUAL_CHECKS)


def review_stage_one(review_path: Path, out: Path = OUT) -> dict:
    with evaluation.execution_lock(out):
        plan, frozen = verified_plan(out)
        destination = out / "stage-one-review.json"
        if destination.exists():
            raise ValueError("阶段核查已保存，禁止改写原结论。")
        path = out / "samples/merged_nonstream.json"
        record = evaluation.read_json(path)
        if record.get("mechanical_gates_passed") is not True:
            raise ValueError("第一阶段机械验收失败，不能标记事实合格或开放第二阶段。")
        review = evaluation.read_json(review_path)
        facts_passed = validate_factual_review(review, record)
        saved = {
            "protocol": PROTOCOL, "stage": "merged_nonstream", "created_at": evaluation.now(),
            "record_sha256": digest(path), "request_id": stage_identity(plan, "merged_nonstream"),
            "request_sha256": frozen["request_sha256"],
            "mechanical_checks": record["mechanical_checks"],
            "factual_review": review, "all_gates_passed": facts_passed,
            "second_stage": "same_body_nonstream_once" if facts_passed else "stopped",
        }
        evaluation.write_json(destination, saved)
        return {"all_gates_passed": facts_passed, "second_stage_permitted": facts_passed,
                "sent_requests": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "prepare", "first", "second",
                                            "review-first", "self-check"))
    parser.add_argument("--review-file", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "preview":
        result = preview()
    elif arguments.command == "prepare":
        result = prepare()
    elif arguments.command == "self-check":
        self_check()
        result = {"self_check_passed": True, "sent_requests": 0}
    elif arguments.command == "review-first":
        if arguments.review_file is None:
            parser.error("review-first需要--review-file，事实核查不能自动假定通过。")
        result = review_stage_one(arguments.review_file)
    else:
        result = execute("merged_nonstream" if arguments.command == "first"
                         else "stability_nonstream")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
