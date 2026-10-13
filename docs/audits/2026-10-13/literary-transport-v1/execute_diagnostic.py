"""One-shot transport diagnosis with frozen inputs and no automatic retry."""
from __future__ import annotations

import argparse
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
PARENT = OUT.parent / "literary-revision-v1"
CONFIG = {"base_url": "https://api.icodexs.com/v1", "model": "glm-5.1",
          "total_timeout": 180, "api_key": ""}
EXPECTED = {
    "frozen-writer-requests.json": "0b155b870a4e1f58170037825a56b051a7c107dcb08cb1c83263667d27a80f87",
    "samples/mystery-tide--1--moyu.json": "8fb372259ab09d5733a124f37a43c78e28f8927156089feb0180e7a7385316a4",
}
BODY_SHA = {False: "729c3fe156b6a98444e9ecb167a2352d01efceb05973bab7ef33603a05a0559c",
            True: "d40358d18f3a42ee812721b3cf236c5c73b7bc5991ef3278a710b8919d5167b4"}
PROVIDER_SHA = "7c73cd1aac355cf83285cdd111952ac35841d735e1eddc3ebdaf94dbbbdb7f95"
PROTOCOL_SHA = "dcabeba9bf4a25f4b1a2d7e2b7a2480a53de00831b8692b0f4309d4ed83dde2e"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_inputs():
    for name, expected in EXPECTED.items():
        if digest(PARENT / name) != expected:
            raise ValueError("Parent evidence changed; no request sent.")
    if digest(ROOT / "moyu_provider.py") != PROVIDER_SHA:
        raise ValueError("Provider source changed; no request sent.")
    frozen = evaluation.read_json(PARENT / "frozen-writer-requests.json")["mystery-tide--moyu"]
    payload = json.loads(frozen["body_utf8"])
    prepared = {}
    for streaming in (False, True):
        item = provider.prepare_chat_request(
            CONFIG, payload["messages"], task="continue", max_tokens=3000,
            stream=streaming, parameters={"temperature": 0.65, "thinking": {"type": "disabled"}})
        if item["request_audit"]["request_sha256"] != BODY_SHA[streaming]:
            raise ValueError("Frozen body differs; no request sent.")
        prepared[str(streaming).lower()] = {
            "body_utf8": item["body"].decode(), "request_sha256": BODY_SHA[streaming],
            "messages_sha256": item["request_audit"]["messages_sha256"],
            "endpoint": item["endpoint"], "payload": item["payload"],
        }
    return prepared


def cumulative():
    rows = []
    for path in sorted(OUT.parent.glob("literary-*/request-ledger.json")):
        rows.extend(evaluation.read_json(path))
    for path in sorted(OUT.parent.glob("provider-shape-*.json")):
        record = evaluation.read_json(path)
        usage = next((event["usage"] for event in reversed(record.get("events", []))
                      if isinstance(event.get("usage"), dict)), None)
        rows.append({"usage": usage})
    accounting = [provider.usage_accounting(row.get("usage"), row.get("usage_accounting"))
                  for row in rows]
    return {
        "requests_reserved": len(rows),
        "complete_tokens": sum(item["total_tokens"] or 0 for item in accounting),
        "known_lower_bound": sum(item["reported_tokens_lower_bound"] for item in accounting),
        "missing_complete_usage": sum(item["state"] != "reported" for item in accounting),
        "request_cap": 60, "known_tokens_stop_threshold": 180000, "currency_cost": "unknown",
    }


class Observer:
    """Observe the same decoded reply; retain counts, never reasoning text."""
    def __init__(self):
        self.body = []
        self.stats = {"events": 0, "content_pieces": 0, "content_characters": 0,
                      "reasoning_nonempty_pieces": 0, "reasoning_characters": 0,
                      "refusal_nonempty_pieces": 0, "unexplained_shapes": 0,
                      "finish_reasons": [], "models": [], "raw_usage": [],
                      "http_status": None, "content_type": None}

    def observe(self, data, sse):
        stats = self.stats
        stats["events"] += 1
        if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
            stats["unexplained_shapes"] += 1
            return
        if data.get("model") and data["model"] not in stats["models"]:
            stats["models"].append(data["model"])
        if data.get("usage") is not None:
            stats["raw_usage"].append(copy.deepcopy(data["usage"]))
        for choice in data["choices"]:
            if not isinstance(choice, dict):
                stats["unexplained_shapes"] += 1
                continue
            finish = choice.get("finish_reason")
            if finish not in (None, ""):
                stats["finish_reasons"].append(finish)
            message = choice.get("delta" if sse else "message")
            if not isinstance(message, dict):
                stats["unexplained_shapes"] += 1
                continue
            for key in ("reasoning_content", "reasoning"):
                value = message.get(key)
                if isinstance(value, str) and value:
                    stats["reasoning_nonempty_pieces"] += 1
                    stats["reasoning_characters"] += len(value)
                elif value is not None and not isinstance(value, str):
                    stats["unexplained_shapes"] += 1
            if message.get("refusal"):
                stats["refusal_nonempty_pieces"] += 1
            content = message.get("content")
            if isinstance(content, str):
                self.body.append(content)
                stats["content_pieces"] += 1
                stats["content_characters"] += len(content)
            elif content is not None:
                stats["unexplained_shapes"] += 1


def self_check():
    observer = Observer()
    observer.observe({"model": "fixture", "choices": [{"message": {
        "content": "A", "reasoning_content": ""}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}, False)
    observer.observe({"choices": [{"delta": {"content": "B", "reasoning_content": "X"},
                                  "finish_reason": None}]}, True)
    observer.observe({"choices": [{"delta": {"content": "C"}, "finish_reason": "length"}]}, True)
    assert "".join(observer.body) == "ABC"
    assert observer.stats["reasoning_characters"] == 1
    assert observer.stats["finish_reasons"] == ["stop", "length"]
    observer.observe({"choices": [{"message": {"content": ["unsupported"]}}]}, False)
    assert observer.stats["unexplained_shapes"] == 1
    assert provider.usage_accounting(None)["state"] != "reported"
    assert provider.usage_accounting({"prompt_tokens": 2, "completion_tokens": 3,
                                      "total_tokens": 6})["state"] != "reported"


def prepare():
    if (OUT / "plan.json").exists():
        raise ValueError("Diagnostic already frozen; no original replaced.")
    inputs = freeze_inputs()
    start = cumulative()
    if (start["requests_reserved"], start["complete_tokens"],
            start["known_lower_bound"], start["missing_complete_usage"]) != (33, 59129, 59129, 10):
        raise ValueError("Cumulative origin differs; no request sent.")
    protocol = ROOT / "artifacts/next-literary-protocol.md"
    if digest(protocol) != PROTOCOL_SHA:
        raise ValueError("Preregistered protocol differs.")
    self_check()
    (OUT / "preregistered-protocol.md").write_bytes(protocol.read_bytes())
    evaluation.write_json(OUT / "frozen-requests.json", inputs)
    evaluation.write_json(OUT / "plan.json", {
        "protocol": "moyu-transport-diagnosis-v1", "run_id": uuid.uuid4().hex,
        "created_at": evaluation.now(), "new_requests_cap": 2,
        "first_stage": "nonstream", "second_stage": "stream_only_after_all_gates_pass",
        "budget": {"max_requests": 2, "reported_tokens_stop_threshold": 120871},
        "cumulative_before": start, "body_sha256": BODY_SHA[False],
        "parent_sha256": EXPECTED, "provider_source_sha256": PROVIDER_SHA,
        "execution_source_sha256": digest(Path(__file__)),
        "preregistered_protocol_sha256": PROTOCOL_SHA,
        "independent_stories_added": 0, "blind_grades": 0,
    })
    print(json.dumps({"prepared": True, "prior": start, "requests_cap": 2}))


def execute(streaming):
    plan = evaluation.read_json(OUT / "plan.json")
    if (plan["execution_source_sha256"] != digest(Path(__file__)) or
            digest(OUT / "preregistered-protocol.md") != PROTOCOL_SHA):
        raise ValueError("Execution or protocol changed; no request sent.")
    inputs = freeze_inputs()
    if inputs != evaluation.read_json(OUT / "frozen-requests.json"):
        raise ValueError("Frozen requests changed; no request sent.")
    if streaming:
        gate = evaluation.read_json(OUT / "stage-one-review.json")
        first = OUT / "samples/nonstream.json"
        if (gate.get("all_gates_passed") is not True or gate.get("record_sha256") != digest(first)
                or not evaluation.read_json(first).get("mechanical_gates_passed")):
            raise ValueError("First stage failed or lacks factual review; second stage stopped.")
    prior = cumulative()
    if prior["requests_reserved"] >= 60 or prior["known_lower_bound"] >= 180000:
        raise ValueError("Cumulative stop threshold reached; no request sent.")
    key = os.environ.get("MOYU_AI_API_KEY")
    if not key:
        raise ValueError("Runtime credential unavailable; no request sent.")
    config = {**CONFIG, "api_key": key}
    identifier = "stream" if streaming else "nonstream"
    path = OUT / "samples" / (identifier + ".json")
    if path.exists():
        raise ValueError("Stage already reserved; no repeat request sent.")
    ledger = evaluation.Ledger(OUT, plan)
    frozen = inputs[str(streaming).lower()]
    request_id = "diag_" + evaluation.sha({"run_id": plan["run_id"], "stage": identifier})[:32]
    parameters = {"temperature": 0.65, "thinking": {"type": "disabled"}}
    prepared = provider.prepare_chat_request(config, frozen["payload"]["messages"], task="continue",
        max_tokens=3000, stream=streaming, parameters=parameters, request_id=request_id)
    assert prepared["request_audit"]["request_sha256"] == frozen["request_sha256"]
    index = ledger.reserve(identifier, "writer", "glm-5.1")
    record = {"id": identifier, "status": "reserved", "started_at": evaluation.now(),
              "request_audit": prepared["request_audit"], "automatic_retry": False,
              "frozen_request_sha256": frozen["request_sha256"]}
    evaluation.write_json(path, record)
    observer = Observer()
    original_payload = provider._Accumulator.payload
    original_response = http.client.HTTPConnection.getresponse

    def observed_payload(accumulator, data, *, sse, **options):
        observer.observe(data, sse)
        return original_payload(accumulator, data, sse=sse, **options)

    def observed_response(connection):
        response = original_response(connection)
        observer.stats["http_status"] = response.status
        observer.stats["content_type"] = response.getheader("Content-Type", "").partition(";")[0].strip()
        return response

    def dispatch(audit):
        ledger.mark_dispatch(index, audit)
        record["request_audit"] = audit
        evaluation.write_json(path, record)

    provider._Accumulator.payload = observed_payload
    http.client.HTTPConnection.getresponse = observed_response
    try:
        result = provider.call_chat_completion(
            config, frozen["payload"]["messages"], task="continue", max_tokens=3000,
            stream=streaming, parameters=parameters, request_id=request_id, on_dispatch=dispatch)
        record.update(status="complete", result=result)
    except provider.ProviderError as error:
        record.update(status=error.status, error_code=error.code, result=error.partial_result)
    except BaseException as error:
        partial = getattr(error, "partial_result", None) or {
            "request_audit": record["request_audit"], "billing_state": "unknown",
            "completion_state": "unknown", "safe_to_apply": False,
        }
        record.update(status="unknown", error_type=type(error).__name__, result=partial)
    finally:
        provider._Accumulator.payload = original_payload
        http.client.HTTPConnection.getresponse = original_response
    result = record["result"]
    case = evaluation.read_json(PARENT / "plan.json")["cases"][0]
    validation = evaluation.sample_validation(case, record)
    stats = provider._redact(observer.stats, key)
    independent = "".join(observer.body)
    stats["body_sha256"] = hashlib.sha256(independent.encode()).hexdigest()
    stats["body_matches_product"] = independent == result.get("text")
    checks = {
        "protocol_output_eligible": validation["eligible_for_blind_review"],
        "complete_usage": provider.usage_accounting(result.get("usage"),
                            result.get("usage_accounting"))["state"] == "reported",
        "mode_observed": stats["content_type"] == ("text/event-stream" if streaming else "application/json"),
        "model_observed": stats["models"] == ["glm-5.1"] and result.get("returned_model") == "glm-5.1",
        "no_nonempty_reasoning": stats["reasoning_nonempty_pieces"] == 0,
        "no_refusal": stats["refusal_nonempty_pieces"] == 0,
        "all_shapes_explained": stats["unexplained_shapes"] == 0,
        "same_body": stats["body_matches_product"],
    }
    record.update(ended_at=evaluation.now(), observer=stats, output_validation=validation,
                  mechanical_checks=checks, mechanical_gates_passed=all(checks.values()),
                  factual_review="pending_only_if_mechanically_eligible")
    ledger.finish(index, result, record["status"])
    record["cumulative_after"] = cumulative()
    evaluation.write_json(path, record)
    (path.with_suffix(".md")).write_text(result.get("text", ""), encoding="utf-8")
    print(json.dumps({"stage": identifier, "status": record["status"],
                      "characters": validation["characters"], "checks": checks,
                      "cumulative": record["cumulative_after"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "nonstream", "stream", "self-check"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "self-check":
        self_check()
        print('{"self_check_passed":true}')
    else:
        execute(args.command == "stream")
