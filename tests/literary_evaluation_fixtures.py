#!/usr/bin/env python3
"""Offline checks for frozen prompts, blinded grading and conservative reports."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import moyu_output
from moyu_provider import prepare_chat_request
from scripts import literary_evaluation as evaluation


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="moyu-literary-fixture-")
        self.history_temporary = tempfile.TemporaryDirectory(prefix="moyu-literary-history-")
        self.out = Path(self.temporary.name)
        self._global_reservation_path = evaluation.GLOBAL_RESERVATION_PATH
        evaluation.GLOBAL_RESERVATION_PATH = self.out / "global-reservations.json"
        self.plan = evaluation.default_plan()
        self.config = {"base_url": "http://127.0.0.1:9/v1", "model": "writer-fixture",
                       "api_key": "fixture-literary-private-credential", "include_usage": True}
        self.plan_path = self.out / "plan.json"
        evaluation.write_json(self.plan_path, self.plan)
        self.calls = []

    def tearDown(self) -> None:
        evaluation.GLOBAL_RESERVATION_PATH = self._global_reservation_path
        self.history_temporary.cleanup()
        self.temporary.cleanup()

    def historical_row(self, request_id: str, request_hash: str,
                       usage: dict | None = None) -> dict:
        return {
            "request_id": request_id,
            "kind": "writer",
            "status": "complete" if usage else "unknown",
            "billing_state": "usage_reported" if usage else "unknown",
            "usage": usage,
            "request_audit": {
                "request_id": request_id,
                "request_sha256": request_hash,
            },
        }

    def history_dir(self, name: str, rows: list[dict]) -> Path:
        path = Path(self.history_temporary.name) / name
        evaluation.write_json(path / "request-ledger.json", rows)
        return path

    def fixture_text(self, task: str) -> str:
        if task == "continue":
            sentence = "风从门缝吹进来，她伸手按住桌沿，等待最后一声脚步停下。"
            return sentence * 26
        if task == "polish":
            return next(case["context"]["current_chapter"]["body"]
                        for case in self.plan["cases"] if case["task"] == "polish")
        return "这一份本机模拟候选只用于验证评测流程。"

    def response(self, config: dict, messages: list[dict], **kwargs: object) -> dict:
        self.calls.append({"model": config["model"], "messages": messages, "options": kwargs})
        prepared = prepare_chat_request(
            config, messages, **{name: kwargs[name] for name in
                ("task", "parameters", "max_tokens", "request_id", "response_format", "stream") if name in kwargs})
        audit = {**prepared["request_audit"], "dispatched": True,
                 "dispatch_state": "dispatch_intent_recorded"}
        callback = kwargs.get("on_dispatch")
        if callable(callback):
            callback(audit)
        if kwargs.get("task") == "literary_judge":
            grade = {"winner": "tie", "scores_A": {key: 3 for key in evaluation.CRITERIA},
                     "scores_B": {key: 3 for key in evaluation.CRITERIA},
                     "hard_violations": [], "rationale": "两份候选按给定规则均未出现确定错误。"}
            text = json.dumps(grade, ensure_ascii=False)
        else:
            grade = None
            text = self.fixture_text(kwargs.get("task"))
        return {"text": text, "structured": grade, "safe_to_apply": True,
                "finish_reason": "stop", "provider_sse_complete": True,
                "completion_state": "complete", "requested_model": config["model"],
                "returned_model": config["model"], "billing_state": "usage_reported",
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                "protocol_observation": {"events_observed": 1, "events_recorded": 1, "events": []},
                "request_audit": audit}

    def config_for(self, model: str) -> dict:
        return {**self.config, "model": model}

    def generated(self) -> None:
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=self.response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture")

    def graded(self, responder=None) -> None:
        evaluation.blind_pairs(self.plan, self.out)
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=responder or self.response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.judge(self.plan, self.out, ["judge-one", "judge-two"])

    def story_coverage_plan(self, count: int = 30, repetitions: int = 1,
                            *, duplicate_stories: bool = False,
                            second_comparator: bool = False) -> None:
        template = self.plan["cases"][0]
        cases = []
        for index in range(count):
            case = copy.deepcopy(template)
            case["id"] = f"coverage-case-{index:02d}"
            case["story_id"] = f"coverage-story-{index // 2 if duplicate_stories else index:02d}"
            case["context"]["current_chapter"]["id"] = case["id"]
            cases.append(case)
        self.plan["cases"] = cases
        self.plan["repetitions"] = repetitions
        if second_comparator:
            self.plan["arms"].append(
                {"id": "baseline-two", "label": "第二个本机对照", "source": "plain-context"})
        arms = len(self.plan["arms"])
        judges = self.plan["judging"]["minimum_distinct_judge_models"]
        self.plan["budget"]["max_requests"] = count * repetitions * (
            arms + (arms - 1) * judges * 2)
        evaluation.validate_plan(self.plan)
        evaluation.write_json(self.plan_path, self.plan)

    def assert_mismatched_sample_identity_is_excluded(self, field: str, value: str) -> None:
        self.story_coverage_plan()
        self.generated()
        self.graded()
        complete = evaluation.report(self.plan, self.out)
        self.assertEqual(complete["distinct_story_coverage"]["reviewed_by_comparator"], {"baseline": 30})
        self.assertTrue(complete["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        case = self.plan["cases"][-1]
        pair = next(pair for pair in evaluation.read_json(self.out / "blind-pairs.json")["pairs"]
                    if pair["case_id"] == case["id"])
        path = self.out / "samples" / (case["id"] + "--1--moyu.json")
        record = evaluation.read_json(path)
        record[field] = value
        evaluation.write_json(path, record)
        evidence_paths = [
            self.out / filename for filename in
            ("run.json", "request-ledger.json", "frozen-writer-requests.json",
             "blind-pairs.json", "blind-key-private.json")]
        evidence_paths += list((self.out / "samples").glob("*.json"))
        evidence_paths += list((self.out / "grades").glob("*.json"))
        before = {path: path.read_bytes() for path in evidence_paths}
        report = evaluation.report(self.plan, self.out)
        self.assertFalse(report["generation"]["output_checks"][record["id"]]["eligible_for_blind_review"])
        self.assertEqual(report["generation"]["eligible_for_blind_review"], 59)
        self.assertEqual(report["judging"]["valid_grades"], 116)
        self.assertEqual(report["judging"]["invalid_or_incomplete_grades"], 4)
        self.assertIn(pair["id"], report["judging"]["invalid_blind_pairs"])
        self.assertEqual(report["judging"]["pairs_with_two_complete_judges"], 29)
        self.assertEqual(report["hard_violation_review"]["candidate_samples_reviewed"], 29)
        self.assertEqual(report["distinct_story_coverage"]["reviewed_by_comparator"], {"baseline": 29})
        self.assertFalse(report["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        self.assertFalse(report["first_tier_proven"])
        with patch.object(evaluation, "call_chat_completion") as transport, \
                self.assertRaisesRegex(ValueError, "匿名候选文本"):
            evaluation.judge(self.plan, self.out, ["judge-one", "judge-two"])
        transport.assert_not_called()
        self.assertEqual(before, {path: path.read_bytes() for path in evidence_paths})

    def test_material_preserves_full_body_and_exact_source_range(self) -> None:
        for case in self.plan["cases"]:
            chapter = case["context"]["current_chapter"]
            self.assertNotIn("body_tail", chapter)
            self.assertEqual(chapter["source_range"], {"start": 0, "end": len(chapter["body"])})
            self.assertEqual(chapter["chapter_hash"], hashlib.sha256(chapter["body"].encode()).hexdigest())
            self.assertTrue(chapter["body_complete"])

    def test_default_writer_plan_uses_the_portable_thinking_switch(self) -> None:
        self.assertEqual(self.plan["writer_parameters"]["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", self.plan["writer_parameters"])
        prepared = prepare_chat_request(
            self.config, [{"role": "user", "content": "只返回正文"}],
            parameters=self.plan["writer_parameters"],
        )
        self.assertEqual(prepared["payload"]["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", prepared["payload"])
        self.assertEqual(prepared["request_audit"]["effective_parameters"]["thinking"],
                         {"type": "disabled"})
        self.assertNotIn("reasoning_effort",
                         prepared["request_audit"]["effective_parameters"])

    def test_plain_context_baseline_uses_shared_output_constraints(self) -> None:
        case = self.plan["cases"][0]
        baseline = next(arm for arm in self.plan["arms"] if arm["source"] == "plain-context")
        messages = evaluation.messages_for(case, baseline, self.plan_path, self.config)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("正文长度目标约725字，允许600至850字。", messages[0]["content"])
        self.assertIn("字数按去除空白后的字符数计算", messages[0]["content"])
        self.assertIn("只返回正文", messages[0]["content"])
        self.assertIn("作者要求：" + case["instruction"], messages[1]["content"])
        self.assertEqual(case["output_contract"]["target_characters"], 725)

    def test_actual_prompt_uses_frozen_contract_without_changing_material(self) -> None:
        for case, contract in (
                (self.plan["cases"][0], {"minimum_characters": 600,
                                        "maximum_characters": 850, "target_characters": 620}),
                (next(case for case in self.plan["cases"] if case["task"] == "polish"),
                 {"minimum_characters": 120, "maximum_characters": 185,
                  "target_characters": 150})):
            with self.subTest(task=case["task"]):
                case = copy.deepcopy(case)
                case["output_contract"] = contract
                before = copy.deepcopy(case)
                constraints = moyu_output.output_prompt_constraints(
                    case["task"], evaluation.output_contract(case))
                for arm in self.plan["arms"]:
                    messages = evaluation.messages_for(case, arm, self.plan_path, self.config)
                    self.assertIn(constraints, messages[0]["content"])
                    self.assertIn(case["instruction"], messages[1]["content"])
                    self.assertNotIn("_output_contract", messages[1]["content"])
                    if arm["source"] == "moyu-prompt":
                        material = json.loads(messages[1]["content"].split(
                            "<manuscript>\n", 1)[1].split("\n</manuscript>", 1)[0])
                    else:
                        material = json.loads(messages[1]["content"].split("\n作者要求：", 1)[0])
                    self.assertEqual(material, case["context"])
                    self.assertEqual(case, before)

    def test_actual_prompt_preserves_legacy_and_unspecified_contracts(self) -> None:
        for explicit in (False, True):
            case = copy.deepcopy(self.plan["cases"][0])
            if explicit:
                case["output_contract"] = {}
            else:
                case.pop("output_contract")
            before = copy.deepcopy(case)
            expected = moyu_output.output_prompt_constraints(
                case["task"], evaluation.output_contract(case))
            for arm in self.plan["arms"]:
                messages = evaluation.messages_for(case, arm, self.plan_path, self.config)
                self.assertIn(expected, messages[0]["content"])
                self.assertEqual("允许600至850字" in messages[0]["content"], not explicit)
            self.assertEqual(case, before)

    def test_frozen_requests_use_same_explicit_contract_in_both_arms(self) -> None:
        self.plan["cases"] = [self.plan["cases"][0]]
        self.plan["cases"][0]["output_contract"]["target_characters"] = 620
        before = copy.deepcopy(self.plan)
        with patch.object(evaluation, "call_chat_completion") as transport:
            evaluation.freeze_run(self.out, self.plan, self.config["model"],
                                  plan_path=self.plan_path, config=self.config)
        transport.assert_not_called()
        requests = evaluation.read_json(self.out / "frozen-writer-requests.json")
        for request in requests.values():
            self.assertIn("正文长度目标约620字，允许600至850字。",
                          request["payload"]["messages"][0]["content"])
            self.assertEqual(request["context_sha256"], evaluation.sha(
                self.plan["cases"][0]["context"]))
            self.assertNotIn("_output_contract", request["body_utf8"])
        self.assertEqual(self.plan, before)

    def test_plan_rejects_boolean_limits_and_missing_rules(self) -> None:
        for key in ("repetitions", "max_output_tokens", "max_judge_output_tokens"):
            plan = copy.deepcopy(self.plan)
            plan[key] = True
            with self.assertRaises(ValueError):
                evaluation.validate_plan(plan)
        plan = copy.deepcopy(self.plan)
        plan["stop_after_first_ineligible_pair"] = "yes"
        with self.assertRaises(ValueError):
            evaluation.validate_plan(plan)
        plan = copy.deepcopy(self.plan)
        plan["cases"][0]["rules"] = []
        with self.assertRaises(ValueError):
            evaluation.validate_plan(plan)

    def test_plan_rejects_invalid_output_contract_and_violation_rate(self) -> None:
        for contract in (
                {"minimum_characters": True, "maximum_characters": 850},
                {"minimum_characters": 850, "maximum_characters": 600},
                {"minimum_characters": 600}, "invalid"):
            plan = copy.deepcopy(self.plan)
            plan["cases"][0]["output_contract"] = contract
            with self.assertRaises(ValueError):
                evaluation.validate_plan(plan)
        for target in (True, 599, 851, 680.0, "680"):
            plan = copy.deepcopy(self.plan)
            plan["cases"][0]["output_contract"]["target_characters"] = target
            with self.subTest(target=target), self.assertRaises(ValueError):
                evaluation.validate_plan(plan)
        for rate in (True, -0.1, 1.1, float("nan"), "0"):
            plan = copy.deepcopy(self.plan)
            plan["acceptance"]["maximum_hard_violation_rate"] = rate
            with self.assertRaises(ValueError):
                evaluation.validate_plan(plan)

    def test_plan_requires_two_blind_judges_and_valid_acceptance_thresholds(self) -> None:
        for minimum in (None, True, 0, 1, 1.5, "2"):
            plan = copy.deepcopy(self.plan)
            plan["judging"]["minimum_distinct_judge_models"] = minimum
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                evaluation.validate_plan(plan)
        for field in ("blind", "both_orders"):
            plan = copy.deepcopy(self.plan)
            plan["judging"][field] = False
            with self.subTest(field=field), self.assertRaises(ValueError):
                evaluation.validate_plan(plan)
        for field in ("minimum_genres", "minimum_distinct_stories_for_tier_claim"):
            for value in (True, 0, -1, 1.5, "3"):
                plan = copy.deepcopy(self.plan)
                plan["acceptance"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    evaluation.validate_plan(plan)
        for value in (True, -0.1, 1.1, float("nan"), float("inf"), "0.4"):
            plan = copy.deepcopy(self.plan)
            plan["acceptance"]["minimum_preference_lower_95"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluation.validate_plan(plan)

    def test_new_plan_requires_a_dedicated_empty_output_directory(self) -> None:
        occupied = self.out / "occupied-run"
        occupied.mkdir()
        sentinel = occupied / "request-ledger.json"
        sentinel.write_text("[]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "独立的空目录"):
            evaluation.main(["plan", "--output-dir", str(occupied)])
        self.assertFalse((occupied / "plan.json").exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "[]\n")

        fresh = self.out / "fresh-run"
        self.assertEqual(evaluation.main(["plan", "--output-dir", str(fresh)]), 0)
        self.assertTrue((fresh / "plan.json").exists())

    def test_sample_validation_enforces_absolute_relative_lengths_and_planning_markers(self) -> None:
        self.generated()
        case = self.plan["cases"][0]
        record = evaluation.read_json(self.out / "samples" / (case["id"] + "--1--moyu.json"))
        self.assertTrue(evaluation.sample_validation(case, record)["eligible_for_blind_review"])
        for text in ("甲" * 599, "甲" * 851, " \n\t"):
            record["result"]["text"] = text
            check = evaluation.sample_validation(case, record)
            self.assertFalse(check["eligible_for_blind_review"])
            self.assertIn("length_out_of_range", {item["code"] for item in check["issues"]})
        for prefix in ("我们需要回答中文。", "创作思路：", "<think>", "We need to write"):
            record["result"]["text"] = prefix + "\n" + "甲" * 650
            check = evaluation.sample_validation(case, record)
            self.assertFalse(check["eligible_for_blind_review"])
            self.assertIn("planning_contamination", {item["code"] for item in check["issues"]})
        polish = next(case for case in self.plan["cases"] if case["task"] == "polish")
        record["result"]["text"] = polish["context"]["current_chapter"]["body"]
        self.assertTrue(evaluation.sample_validation(polish, record)["eligible_for_blind_review"])
        record["result"]["text"] *= 2
        self.assertFalse(evaluation.sample_validation(polish, record)["eligible_for_blind_review"])
        legacy_case = copy.deepcopy(case)
        legacy_case.pop("output_contract")
        self.assertEqual(evaluation.output_contract(legacy_case)["minimum_characters"], 600)

    def test_sample_validation_rejects_inconsistent_truncation_and_sse_tail(self) -> None:
        case = self.plan["cases"][0]
        base = {
            "status": "complete",
            "result": {
                "text": "甲" * 725,
                "safe_to_apply": True,
                "completion_state": "complete",
                "finish_reason": "stop",
            },
        }
        truncated = copy.deepcopy(base)
        truncated["result"]["truncated"] = True
        check = evaluation.sample_validation(case, truncated)
        self.assertFalse(check["protocol_complete"])
        self.assertFalse(check["eligible_for_blind_review"])
        self.assertIn("protocol_incomplete", {item["code"] for item in check["issues"]})

        unterminated_sse = copy.deepcopy(base)
        unterminated_sse["result"].update({
            "stream_transport": "provider_native_sse",
            "provider_sse_complete": False,
        })
        check = evaluation.sample_validation(case, unterminated_sse)
        self.assertFalse(check["protocol_complete"])
        self.assertFalse(check["eligible_for_blind_review"])
        self.assertIn("protocol_incomplete", {item["code"] for item in check["issues"]})

        legacy_stream_audit = copy.deepcopy(base)
        legacy_stream_audit["result"].update({
            "request_audit": {"effective_parameters": {"stream": True}},
            "provider_sse_complete": False,
        })
        check = evaluation.sample_validation(case, legacy_stream_audit)
        self.assertFalse(check["protocol_complete"])
        self.assertFalse(check["eligible_for_blind_review"])
        self.assertIn("protocol_incomplete", {item["code"] for item in check["issues"]})

    def test_unfrozen_length_requirements_use_shared_chinese_rules(self) -> None:
        case = copy.deepcopy(self.plan["cases"][0])
        case.pop("output_contract")
        record = {"status": "complete", "result": {
            "text": "铁", "safe_to_apply": True,
            "completion_state": "complete", "finish_reason": "stop",
        }}
        for instruction in ("续写六百到八百五十字。", "字数控制在600至850个汉字。"):
            with self.subTest(instruction=instruction):
                case["instruction"] = instruction
                before = copy.deepcopy(case)
                with patch.object(evaluation, "prose_output_contract",
                                  wraps=moyu_output.output_contract) as shared:
                    check = evaluation.sample_validation(case, record)
                shared.assert_called_once()
                self.assertEqual(check["output_contract"]["minimum_characters"], 600)
                self.assertEqual(check["output_contract"]["maximum_characters"], 850)
                self.assertFalse(check["eligible_for_blind_review"])
                self.assertEqual(case, before)
        case["instruction"] = "正文至少六百字。"
        record["result"]["text"] = "甲" * 600
        self.assertTrue(evaluation.sample_validation(case, record)["eligible_for_blind_review"])
        case["instruction"] = "正文最多八百五十字。"
        record["result"]["text"] = "甲" * 851
        self.assertFalse(evaluation.sample_validation(case, record)["eligible_for_blind_review"])
        case["instruction"] = "续写，保留六百字的碑文。"
        self.assertEqual(evaluation.output_contract(case)["source"], "unspecified")
        case["output_contract"] = {"minimum_characters": 600, "maximum_characters": 850}
        case["instruction"] = "续写六百百字。"
        self.assertEqual(evaluation.output_contract(case)["source"], "frozen_output_contract")

    def test_soft_target_is_preserved_for_frozen_literary_cases_without_changing_hard_gate(self) -> None:
        case = copy.deepcopy(self.plan["cases"][0])
        case.pop("output_contract")
        case["instruction"] = "续写600至850字。目标约680字。保持林岚视角。"
        derived = evaluation.output_contract(case)
        self.assertEqual(derived["target_characters"], 680)
        self.assertEqual((derived["minimum_characters"], derived["maximum_characters"]),
                         (600, 850))

        case["output_contract"] = {
            key: derived[key]
            for key in ("minimum_characters", "maximum_characters", "target_characters")
        }
        frozen = evaluation.output_contract(case)
        self.assertEqual(frozen["target_characters"], 680)
        baseline = next(arm for arm in self.plan["arms"] if arm["source"] == "plain-context")
        messages = evaluation.messages_for(case, baseline, self.plan_path, self.config)
        self.assertIn("正文长度目标约680字，允许600至850字。", messages[0]["content"])

        record = {"status": "complete", "result": {
            "text": "甲" * 680, "safe_to_apply": True,
            "completion_state": "complete", "finish_reason": "stop",
        }}
        self.assertTrue(evaluation.sample_validation(case, record)["eligible_for_blind_review"])
        for size in (599, 851):
            record["result"]["text"] = "甲" * size
            check = evaluation.sample_validation(case, record)
            self.assertFalse(check["eligible_for_blind_review"])
            self.assertIn("length_out_of_range", {item["code"] for item in check["issues"]})

    def test_sample_validation_uses_shared_writing_markers_and_preserves_original_text(self) -> None:
        case = self.plan["cases"][0]
        for prefix in (
            "我们需要分析任务。用户说要续写当前章节。",
            "我们得精心写一段，保持紧张。",
            "让我构思情节：",
            "让我构思一个约750字的续写：",
            "我们要续写，推进具体冲突。",
        ):
            with self.subTest(prefix=prefix):
                text = prefix + "\n" + "甲" * 650
                record = {
                    "status": "complete",
                    "result": {"text": text, "safe_to_apply": True,
                               "completion_state": "complete", "finish_reason": "stop"},
                }
                before = copy.deepcopy(record)
                with patch.object(evaluation, "find_planning_contamination",
                                  wraps=moyu_output.find_planning_contamination) as shared:
                    check = evaluation.sample_validation(case, record)
                shared.assert_called_once_with(case["task"], text)
                self.assertFalse(check["eligible_for_blind_review"])
                self.assertIn(moyu_output.find_planning_contamination(case["task"], text),
                              check["issues"])
                self.assertEqual(record, before)

    def test_sample_validation_blocks_reasoning_field_when_request_disabled_it(self) -> None:
        case = self.plan["cases"][0]
        text = "甲" * 650
        record = {
            "status": "complete",
            "result": {
                "text": text,
                "safe_to_apply": True,
                "completion_state": "complete",
                "finish_reason": "stop",
                "provider_sse_complete": True,
                "request_audit": {
                    "effective_parameters": {
                        "thinking": {"type": "disabled"},
                        "stream": True,
                    },
                },
                "protocol_observation": {
                    "events": [{
                        "choices": [{
                            "delta": {
                                "fields": {
                                    "reasoning_content": {"type": "string", "chars": 12},
                                },
                            },
                        }],
                    }],
                },
            },
        }
        before = copy.deepcopy(record)
        check = evaluation.sample_validation(case, record)
        self.assertFalse(check["eligible_for_blind_review"])
        self.assertIn("reasoning_content_observed", {item["code"] for item in check["issues"]})
        self.assertEqual(record, before)

        record["result"]["request_audit"]["effective_parameters"] = {
            "reasoning_effort": "none",
            "stream": True,
        }
        check = evaluation.sample_validation(case, record)
        self.assertFalse(check["eligible_for_blind_review"])
        self.assertIn("reasoning_content_observed", {item["code"] for item in check["issues"]})

        record["result"]["request_audit"]["effective_parameters"] = {"stream": True}
        self.assertTrue(evaluation.sample_validation(case, record)["eligible_for_blind_review"])

    def test_reasoning_summary_covers_events_missing_from_bounded_shapes(self) -> None:
        case = self.plan["cases"][0]
        record = {
            "status": "complete",
            "result": {
                "text": "甲" * 650,
                "safe_to_apply": True,
                "completion_state": "complete",
                "finish_reason": "stop",
                "provider_sse_complete": True,
                "request_audit": {"effective_parameters": {
                    "thinking": {"type": "disabled"}, "stream": True,
                }},
                "reasoning_observation": {
                    "field_events": 130, "nonempty_events": 1, "characters": 12,
                    "requested_disabled": True, "unexpected_reasoning": True,
                },
                "protocol_observation": {
                    "events_truncated": True, "events": [],
                },
            },
        }
        before = copy.deepcopy(record)
        check = evaluation.sample_validation(case, record)
        self.assertTrue(check["protocol_complete"])
        self.assertEqual([issue["code"] for issue in check["issues"]],
                         ["reasoning_content_observed"])
        self.assertFalse(check["eligible_for_blind_review"])
        self.assertEqual(record, before)
        record["result"].pop("request_audit")
        self.assertFalse(evaluation.sample_validation(case, record)["eligible_for_blind_review"])
        record["result"]["request_audit"] = {
            "effective_parameters": {"thinking": {"type": "enabled"}},
        }
        self.assertTrue(evaluation.sample_validation(case, record)["eligible_for_blind_review"])

    def test_reasoning_summary_empty_values_and_malformed_legacy_shapes_are_safe(self) -> None:
        record = {"result": {
            "request_audit": {"effective_parameters": {"reasoning_effort": "none"}},
            "reasoning_observation": {
                "nonempty_events": 0, "requested_disabled": True,
            },
            "protocol_observation": {"events": [{"choices": [{
                "message": {"fields": {"reasoning_content": {"type": "string", "chars": 2}}},
            }]}]},
        }}
        self.assertIsNone(evaluation.nonempty_reasoning_observation(record))
        for summary in (None, [], {"nonempty_events": True}, {"nonempty_events": -1}):
            for events in (None, {}, [None], [{"choices": None}], [{"choices": [None]}]):
                with self.subTest(summary=summary, events=events):
                    record["result"]["reasoning_observation"] = summary
                    record["result"]["protocol_observation"] = {"events": events}
                    self.assertIsNone(evaluation.nonempty_reasoning_observation(record))

    def test_reasoning_alias_and_invalid_types_block_complete_eligible_length_samples(self) -> None:
        case = self.plan["cases"][0]
        record = {"status": "complete", "result": {
            "text": "甲" * 725, "safe_to_apply": True,
            "completion_state": "complete", "finish_reason": "stop",
            "request_audit": {"effective_parameters": {"thinking": {"type": "disabled"}}},
        }}
        for field, shape in (
                ("reasoning", {"type": "string", "chars": 12}),
                ("reasoning_content", {"type": "object", "fields": 1}),
                ("reasoning", {"type": "array", "items": 0}),
                ("reasoning_content", {"type": "bool"}),
                ("reasoning", {"type": "int"})):
            for summary in (None, {"nonempty_events": 0, "requested_disabled": True},
                            {"nonempty_events": False, "invalid_events": 0},
                            {"nonempty_events": 0, "invalid_events": -1}):
                with self.subTest(field=field, shape=shape["type"], summary=summary):
                    record["result"]["reasoning_observation"] = summary
                    record["result"]["protocol_observation"] = {"events": [{"choices": [{
                        "message": {"fields": {field: shape}},
                    }]}]}
                    before = copy.deepcopy(record)
                    check = evaluation.sample_validation(case, record)
                    self.assertTrue(check["protocol_complete"])
                    self.assertEqual(check["characters"], 725)
                    self.assertFalse(check["eligible_for_blind_review"])
                    self.assertEqual([item["code"] for item in check["issues"]],
                                     ["reasoning_content_observed"])
                    if shape["type"] != "string":
                        self.assertIn("思考字段类型无法校验", check["issues"][0]["description"])
                        self.assertNotIn("非空", check["issues"][0]["description"])
                    self.assertEqual(record, before)
        record["result"]["protocol_observation"] = {"events_truncated": True, "events": []}
        record["result"]["reasoning_observation"] = {
            "nonempty_events": 0, "invalid_events": 1, "requested_disabled": True,
        }
        self.assertFalse(evaluation.sample_validation(case, record)["eligible_for_blind_review"])
        record["result"]["request_audit"]["effective_parameters"] = {"thinking": {"type": "enabled"}}
        self.assertTrue(evaluation.sample_validation(case, record)["eligible_for_blind_review"])

    def test_old_zero_summary_preserves_whitespace_but_new_counts_are_authoritative(self) -> None:
        record = {"result": {
            "request_audit": {"effective_parameters": {"reasoning_effort": "none"}},
            "reasoning_observation": {"nonempty_events": 0, "requested_disabled": True},
            "protocol_observation": {"events": [{"choices": [{
                "message": {"fields": {
                    "reasoning_content": {"type": "string", "chars": 2},
                    "reasoning": {"type": "null"},
                }},
            }]}]},
        }}
        self.assertIsNone(evaluation.nonempty_reasoning_observation(record))
        record["result"]["protocol_observation"]["events"][0]["choices"][0]["message"]["fields"][
            "reasoning"] = {"type": "string", "chars": 3}
        self.assertIsNotNone(evaluation.nonempty_reasoning_observation(record))
        record["result"]["reasoning_observation"]["invalid_events"] = 0
        self.assertIsNone(evaluation.nonempty_reasoning_observation(record))

    def test_sample_validation_accepts_quoted_markers_and_ordinary_novel_monologues(self) -> None:
        case = self.plan["cases"][0]
        for prose in (
            "“我们需要分析任务。”她照着信上的句子读。",
            "「我们得精心写一段，保持紧张。」她读完便合上剧本。",
            "'让我构思情节：'他说，随后把纸翻过来。",
            "她把写有<think>的卡片塞进抽屉。",
            "“<think>是卡片上的印字。”她说。",
            "我们需要分析脚印。海水已冲走最外侧的半枚鞋印。",
            "我们需要分析任务分配留下的痕迹，才能找到失踪的人。",
            "我们得精心写一段证词，父亲才能洗清冤屈。",
            "让我构思情节的是死者桌上那只没洗的杯子。",
            "林岚盯着纸上那句话：让我构思情节。她认出了字迹。",
        ):
            with self.subTest(prose=prose):
                record = {
                    "status": "complete",
                    "result": {"text": prose + "\n" + "甲" * 650, "safe_to_apply": True,
                               "completion_state": "complete", "finish_reason": "stop"},
                }
                before = copy.deepcopy(record)
                check = evaluation.sample_validation(case, record)
                self.assertTrue(check["eligible_for_blind_review"], check)
                self.assertEqual(record, before)

    def test_v5_archived_body_is_detected_without_rewriting_historical_record(self) -> None:
        path = (ROOT / "docs" / "audits" / "2026-10-13" / "literary-pilot-v5"
                / "samples" / "mystery-tide--1--moyu.json")
        if not path.exists():
            self.skipTest("完整历史评测归档未包含在当前工作副本；代表性开头另有固定回归覆盖。")
        original = path.read_bytes()
        record = json.loads(original)
        before = copy.deepcopy(record)
        self.assertTrue(record["result"]["text"].startswith("我们需要分析任务。"))
        case = self.plan["cases"][0]
        check = evaluation.sample_validation(case, record)
        finding = next(item for item in check["issues"] if item["code"] == "planning_contamination")
        self.assertEqual(finding["quote"], "我们需要分析任务")
        self.assertFalse(check["eligible_for_blind_review"])
        self.assertEqual(record, before)
        self.assertEqual(path.read_bytes(), original)

    def test_blind_skips_complete_but_invalid_outputs_and_partial_generation(self) -> None:
        def short_response(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            result["text"] = "只有一句正文。"
            return result
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=short_response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=2)
        record = evaluation.read_json(self.out / "samples" / "mystery-tide--1--moyu.json")
        self.assertEqual(record["status"], "complete")
        self.assertFalse(record["sample_validation"]["eligible_for_blind_review"])
        blind = evaluation.blind_pairs(self.plan, self.out)
        self.assertEqual(blind["pairs"], [])
        self.assertEqual(blind["pairs_excluded_by_output_or_missing_samples"], len(self.plan["cases"]))
        result = evaluation.report(self.plan, self.out)
        self.assertEqual(result["generation"]["complete_samples"], 2)
        self.assertEqual(result["generation"]["eligible_for_blind_review"], 0)
        self.assertFalse(result["acceptance_gates"]["all_sample_outputs_eligible"])
        self.assertFalse(result["pilot_quality_passed"])

    def test_generation_stops_after_first_ineligible_pair_without_manual_request_cap(self) -> None:
        def short_response(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            result["text"] = "只有一句正文。"
            return result
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=short_response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(evaluation.read_json(self.out / "request-ledger.json")), 2)
        self.assertEqual(sorted(path.name for path in (self.out / "samples").glob("*.json")),
                         ["mystery-tide--1--baseline.json", "mystery-tide--1--moyu.json"])
        evaluation.blind_pairs(self.plan, self.out)
        report = evaluation.report(self.plan, self.out)
        self.assertEqual(report["generation"]["recorded_samples"], 2)
        self.assertFalse(report["acceptance_gates"]["all_sample_outputs_eligible"])
        self.assertFalse(report["first_tier_proven"])

    def test_report_rechecks_tampered_samples_instead_of_trusting_saved_validation(self) -> None:
        self.generated()
        self.graded()
        path = self.out / "samples" / "mystery-tide--1--moyu.json"
        record = evaluation.read_json(path)
        self.assertTrue(record["sample_validation"]["eligible_for_blind_review"])
        record["result"]["text"] = "我们需要创作候选正文。\n" + "甲" * 650
        evaluation.write_json(path, record)
        before = path.read_bytes()
        result = evaluation.report(self.plan, self.out)
        self.assertEqual(result["generation"]["complete_samples"], 12)
        self.assertEqual(result["generation"]["eligible_for_blind_review"], 11)
        self.assertEqual(result["judging"]["valid_grades"], 20)
        self.assertFalse(result["pilot_evidence_complete"])
        self.assertFalse(result["pilot_quality_passed"])
        self.assertEqual(path.read_bytes(), before)

    def test_judge_rejects_tampered_blind_text_before_reserving_or_dispatching(self) -> None:
        self.generated()
        evaluation.blind_pairs(self.plan, self.out)
        path = self.out / "blind-pairs.json"
        blind = evaluation.read_json(path)
        blind["pairs"][0]["A"] += "外部改写。"
        evaluation.write_json(path, blind)
        ledger_bytes = (self.out / "request-ledger.json").read_bytes()
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion") as transport, \
                self.assertRaisesRegex(ValueError, "匿名候选文本"):
            evaluation.judge(self.plan, self.out, ["judge-one", "judge-two"])
        transport.assert_not_called()
        self.assertEqual((self.out / "request-ledger.json").read_bytes(), ledger_bytes)

    def test_changed_candidates_cannot_reuse_prior_grade_snapshot(self) -> None:
        self.generated()
        self.graded()
        path = self.out / "samples" / "mystery-tide--1--moyu.json"
        record = evaluation.read_json(path)
        record["result"]["text"] = record["result"]["text"].replace("风", "雨", 1)
        evaluation.write_json(path, record)
        evaluation.blind_pairs(self.plan, self.out)
        ledger_bytes = (self.out / "request-ledger.json").read_bytes()
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion") as transport, \
                self.assertRaisesRegex(ValueError, "当前匿名候选快照"):
            evaluation.judge(self.plan, self.out, ["judge-one", "judge-two"])
        transport.assert_not_called()
        self.assertEqual((self.out / "request-ledger.json").read_bytes(), ledger_bytes)
        result = evaluation.report(self.plan, self.out)
        self.assertEqual(result["judging"]["valid_grades"], 20)
        self.assertFalse(result["pilot_evidence_complete"])

    def test_single_valid_judge_hard_violation_is_preserved_without_both_orders(self) -> None:
        self.generated()
        evaluation.blind_pairs(self.plan, self.out)
        def flagged(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            pair = json.loads(messages[1]["content"])
            key = next(item for item in evaluation.read_json(self.out / "blind-key-private.json")
                       if item["id"] == pair["id"])
            side = "A" if key["A"] == self.plan["candidate_arm"] else "B"
            result["structured"]["hard_violations"] = [{
                "candidate": side, "rule_id": pair["rules"][0]["id"],
                "quote": pair[side][:24], "explanation": "本机模拟评审标记，仅检验统计闸。"}]
            return result
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=flagged), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.judge(self.plan, self.out, ["judge-one", "judge-two"], max_new_requests=1)
        result = evaluation.report(self.plan, self.out)
        self.assertEqual(result["judging"]["valid_grades"], 1)
        self.assertEqual(result["hard_violation_review"]["candidate_samples_reviewed"], 0)
        self.assertEqual(result["hard_violation_review"]["samples_flagged_by_judge"],
                         ["mystery-tide--1--moyu"])
        self.assertEqual(result["hard_violation_review"]["samples_flagged_without_complete_review"],
                         ["mystery-tide--1--moyu"])
        self.assertFalse(result["acceptance_gates"]["no_judge_flagged_hard_violation"])
        self.assertFalse(result["acceptance_gates"]["hard_violation_rate_within_gate"])

    def test_complete_evidence_with_real_quote_violations_is_not_quality_passed(self) -> None:
        self.generated()
        def flagged(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            pair = json.loads(messages[1]["content"])
            result["structured"]["hard_violations"] = [{
                "candidate": "A", "rule_id": pair["rules"][0]["id"],
                "quote": pair["A"][:24], "explanation": "本机模拟事实违规。"}]
            return result
        self.graded(flagged)
        result = evaluation.report(self.plan, self.out)
        self.assertTrue(result["pilot_evidence_complete"])
        self.assertFalse(result["acceptance_gates"]["hard_violation_rate_within_gate"])
        self.assertFalse(result["pilot_quality_passed"])
        self.assertGreater(result["hard_violation_review"]["rate_among_reviewed"], 0)

    def test_consistent_candidate_wins_pass_pilot_quality_without_claiming_first_tier(self) -> None:
        self.generated()
        def preferred(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            pair = json.loads(messages[1]["content"])
            key = next(item for item in evaluation.read_json(self.out / "blind-key-private.json")
                       if item["id"] == pair["id"])
            side = "A" if key["A"] == self.plan["candidate_arm"] else "B"
            records = [evaluation.read_json(path) for path in (self.out / "grades").glob("*.json")]
            current = next(item for item in records if item.get("status") == "reserved"
                           and item["request_audit"]["request_id"] == kwargs["request_id"])
            if current["order"] == "BA":
                side = "A" if side == "B" else "B"
            result["structured"]["winner"] = side
            return result
        self.graded(preferred)
        result = evaluation.report(self.plan, self.out)
        self.assertTrue(result["pilot_evidence_complete"])
        self.assertTrue(result["pilot_quality_passed"])
        self.assertTrue(result["acceptance_gates"]["hard_violation_rate_within_gate"])
        self.assertTrue(result["acceptance_gates"]["preference_uncertainty_within_gate"])
        self.assertEqual(result["judging"]["paired_preference"]["baseline"]["wins"], 6)
        self.assertFalse(result["first_tier_proven"])

    def test_two_judge_aliases_returning_one_model_do_not_prove_two_judges(self) -> None:
        self.generated()
        def aliases(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            result["returned_model"] = "one-actual-judge"
            return result
        self.graded(aliases)
        result = evaluation.report(self.plan, self.out)
        self.assertEqual(result["judging"]["valid_grades"], 24)
        self.assertEqual(result["judging"]["distinct_returned_judge_models"], ["one-actual-judge"])
        self.assertEqual(result["judging"]["pairs_with_two_complete_judges"], 0)
        self.assertFalse(result["pilot_evidence_complete"])

    def test_judge_alias_returning_writer_is_invalid_evidence(self) -> None:
        self.generated()
        def alias(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            result["returned_model"] = "writer-fixture"
            return result
        self.graded(alias)
        result = evaluation.report(self.plan, self.out)
        self.assertEqual(result["judging"]["valid_grades"], 0)
        self.assertEqual(result["judging"]["invalid_or_incomplete_grades"], 24)
        self.assertFalse(result["pilot_evidence_complete"])

    def test_generation_roundtrip_preserves_actual_request_bytes_and_never_retries(self) -> None:
        self.generated()
        before = len(self.calls)
        with patch.object(evaluation, "messages_for", side_effect=AssertionError("prompt regeneration")):
            self.generated()
        self.assertEqual(len(self.calls), before)
        frozen = evaluation.read_json(self.out / "frozen-writer-requests.json")
        for record in (self.out / "samples").glob("*.json"):
            sample = evaluation.read_json(record)
            snapshot = frozen[f"{sample['case_id']}--{sample['arm_id']}"]
            self.assertEqual(hashlib.sha256(snapshot["body_utf8"].encode()).hexdigest(),
                             snapshot["request_sha256"])
            self.assertEqual(sample["result"]["request_audit"]["request_sha256"],
                             snapshot["request_sha256"])
        self.assertNotIn(self.config["api_key"], (self.out / "frozen-writer-requests.json").read_text())
        self.assertNotIn(self.config["api_key"], (self.out / "request-ledger.json").read_text())

    def test_all_evaluation_artifacts_redact_provider_credentials(self) -> None:
        self.generated()
        self.graded()
        paths = [path for path in self.out.rglob("*") if path.is_file()]
        self.assertGreater(len(paths), 0)
        secret = self.config["api_key"].encode("utf-8")
        for path in paths:
            with self.subTest(path=path.relative_to(self.out)):
                self.assertNotIn(secret, path.read_bytes())

    def test_freeze_persists_one_run_identity_and_new_freeze_has_another(self) -> None:
        first = evaluation.freeze_run(self.out, self.plan, "writer-fixture",
                                      plan_path=self.plan_path, config=self.config)
        paths = [self.out / "run.json", self.out / "frozen-writer-requests.json"]
        before = [path.read_bytes() for path in paths]
        restored = evaluation.freeze_run(self.out, self.plan, "writer-fixture",
                                         plan_path=self.plan_path, config=self.config)
        self.assertRegex(first["run_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(first, restored)
        self.assertEqual(before, [path.read_bytes() for path in paths])
        second = evaluation.freeze_run(self.out / "second-run", self.plan, "writer-fixture",
                                       plan_path=self.plan_path, config=self.config)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["writer_requests_sha256"], second["writer_requests_sha256"])

    def test_same_writer_samples_in_new_runs_have_distinct_external_request_ids(self) -> None:
        second_out = self.out / "second-run"
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=self.response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=2)
            evaluation.generate(self.plan, self.plan_path, second_out, "writer-fixture", max_new_requests=2)
        first, second = self.calls[:2], self.calls[2:]
        self.assertTrue({call["options"]["request_id"] for call in first}.isdisjoint(
            call["options"]["request_id"] for call in second))
        first_sample = evaluation.read_json(next((self.out / "samples").glob("*.json")))
        second_sample = evaluation.read_json(second_out / "samples" / (first_sample["id"] + ".json"))
        self.assertEqual(first_sample["id"], second_sample["id"])
        self.assertEqual(first_sample["frozen_request_sha256"], second_sample["frozen_request_sha256"])
        self.assertNotEqual(first_sample["run_id"], second_sample["run_id"])
        self.assertNotEqual(first_sample["request_audit"]["request_id"],
                            second_sample["request_audit"]["request_id"])

    def test_legacy_run_without_identity_cannot_dispatch_or_rewrite_writer_records(self) -> None:
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=self.response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=1)
        run_path = self.out / "run.json"
        run = evaluation.read_json(run_path)
        run.pop("run_id")
        evaluation.write_json(run_path, run)
        paths = [run_path, self.out / "request-ledger.json",
                 self.out / "frozen-writer-requests.json", *sorted((self.out / "samples").glob("*.json"))]
        before = [path.read_bytes() for path in paths]
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion") as transport, \
                self.assertRaisesRegex(ValueError, "独立运行身份"):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=1)
        transport.assert_not_called()
        self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_same_stream_setting_and_usage_request_apply_to_all_writer_arms(self) -> None:
        self.plan["writer_stream"] = True
        self.generated()
        frozen = evaluation.read_json(self.out / "frozen-writer-requests.json")
        self.assertTrue(all(item["payload"]["stream"] for item in frozen.values()))
        self.assertTrue(all(item["payload"]["stream_options"]["include_usage"] for item in frozen.values()))
        self.assertTrue(all(call["options"]["stream"] for call in self.calls))

    def test_two_unknown_failures_stop_without_reserving_or_dispatching_a_third(self) -> None:
        def failed(*_args, **_kwargs):
            return {"safe_to_apply": False, "text": "", "completion_state": "unknown",
                    "usage": None, "billing_state": "unknown"}
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=failed) as transport, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture")
        self.assertEqual(transport.call_count, 2)
        rows = evaluation.read_json(self.out / "request-ledger.json")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["billing_state"] == "unknown" for row in rows))

    def test_keyboard_interrupt_finishes_reserved_record_as_unknown_without_retry(self) -> None:
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture")
        rows = evaluation.read_json(self.out / "request-ledger.json")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "unknown")
        self.assertEqual(rows[0]["billing_state"], "unknown")
        sample = evaluation.read_json(next((self.out / "samples").glob("*.json")))
        self.assertEqual(sample["status"], "unknown")
        self.assertIsNone(sample["result"]["request_audit"]["dispatched"])
        self.assertFalse(sample["result"]["safe_to_apply"])

    def test_dispatch_marks_ledger_and_sample_before_provider_result(self) -> None:
        observations = []
        def inspect(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            rows = evaluation.read_json(self.out / "request-ledger.json")
            row = rows[-1]
            sample = evaluation.read_json(self.out / "samples" / (row["request_id"] + ".json"))
            self.assertEqual(row["status"], "reserved")
            self.assertEqual(sample["status"], "reserved")
            self.assertTrue(row["request_audit"]["dispatched"])
            self.assertEqual(row["request_audit"], sample["request_audit"])
            observations.append(row["request_id"])
            return result
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=inspect), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=2)
        self.assertEqual(len(observations), 2)

    def test_two_new_request_limit_pauses_and_resume_uses_only_new_ids(self) -> None:
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=self.response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=2)
            first = [item["options"]["request_id"] for item in self.calls]
            first_run = (self.out / "run.json").read_bytes()
            self.assertEqual(len(evaluation.read_json(self.out / "request-ledger.json")), 2)
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=2)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(set(first).isdisjoint(item["options"]["request_id"] for item in self.calls[2:]))
        self.assertEqual(len(evaluation.read_json(self.out / "request-ledger.json")), 4)
        self.assertEqual((self.out / "run.json").read_bytes(), first_run)

    def test_abrupt_exit_after_dispatch_recovers_unknown_with_checkpoint_and_no_retry(self) -> None:
        def terminate(config, messages, **kwargs):
            self.response(config, messages, **kwargs)
            raise SystemExit("fixture abrupt process exit")
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=terminate), \
                self.assertRaises(SystemExit):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=1)
        original = evaluation.read_json(self.out / "request-ledger.json")[0]
        self.assertEqual(original["status"], "reserved")
        ledger = evaluation.Ledger(self.out, self.plan)
        self.assertEqual(ledger.rows[0]["status"], "unknown")
        self.assertEqual(ledger.rows[0]["billing_state"], "unknown")
        checkpoint = evaluation.read_json(self.out / ledger.rows[0]["reconciliation"]["checkpoint"])
        self.assertEqual(checkpoint["original_ledger"], original)
        self.assertEqual(checkpoint["original_record"]["status"], "reserved")
        sample = evaluation.read_json(self.out / "samples" / (original["request_id"] + ".json"))
        self.assertEqual(sample["status"], "unknown")
        self.assertTrue(sample["result"]["request_audit"]["dispatched"])
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=self.response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=1)
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(self.calls[0]["options"]["request_id"], self.calls[1]["options"]["request_id"])

    def test_old_reserved_without_dispatch_marker_is_preserved(self) -> None:
        ledger = evaluation.Ledger(self.out, self.plan)
        ledger.reserve("old-unknown-reservation", "writer", "writer-fixture")
        before = (self.out / "request-ledger.json").read_bytes()
        recovered = evaluation.Ledger(self.out, self.plan)
        self.assertEqual(recovered.rows[0]["status"], "reserved")
        self.assertEqual((self.out / "request-ledger.json").read_bytes(), before)
        self.assertFalse((self.out / "reconciliations").exists())

    def test_interrupt_after_dispatch_preserves_known_send_marker(self) -> None:
        def interrupt(config, messages, **kwargs):
            self.response(config, messages, **kwargs)
            raise KeyboardInterrupt
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=interrupt), \
                self.assertRaises(KeyboardInterrupt):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=1)
        row = evaluation.read_json(self.out / "request-ledger.json")[0]
        sample = evaluation.read_json(self.out / "samples" / (row["request_id"] + ".json"))
        self.assertEqual(row["status"], "unknown")
        self.assertTrue(row["request_audit"]["dispatched"])
        self.assertEqual(row["request_audit"], sample["result"]["request_audit"])
        self.assertFalse(sample["interruption"]["after_response"])

    def test_interrupt_before_complete_response_preserves_received_usage_and_partial_text(self) -> None:
        def interrupt(config, messages, **kwargs):
            partial = self.response(config, messages, **kwargs)
            partial.update({"text": "只有已经收到的部分正文", "safe_to_apply": False,
                            "completion_state": "unknown"})
            interruption = KeyboardInterrupt()
            interruption.partial_result = partial
            raise interruption
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=interrupt), \
                self.assertRaises(KeyboardInterrupt):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=1)
        row = evaluation.read_json(self.out / "request-ledger.json")[0]
        sample = evaluation.read_json(self.out / "samples" / (row["request_id"] + ".json"))
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["usage"]["total_tokens"], 3)
        self.assertEqual(row["billing_state"], "usage_reported")
        self.assertEqual(sample["result"]["text"], "只有已经收到的部分正文")
        self.assertFalse(sample["result"]["safe_to_apply"])
        self.assertEqual(sample["result"]["protocol_observation"]["events_observed"], 1)
        self.assertFalse(sample["interruption"]["after_response"])

    def test_writer_interrupt_during_final_ledger_save_preserves_complete_response(self) -> None:
        original = evaluation.Ledger.finish
        interrupted = []
        def fail_once(ledger, index, result, status):
            if not interrupted:
                interrupted.append(True)
                raise KeyboardInterrupt
            return original(ledger, index, result, status)
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=self.response), \
                patch.object(evaluation.Ledger, "finish", new=fail_once), \
                self.assertRaises(KeyboardInterrupt):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture", max_new_requests=1)
        row = evaluation.read_json(self.out / "request-ledger.json")[0]
        sample = evaluation.read_json(self.out / "samples" / (row["request_id"] + ".json"))
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["usage"]["total_tokens"], 3)
        self.assertTrue(sample["result"]["safe_to_apply"])
        self.assertEqual(sample["result"]["text"], self.fixture_text("continue"))
        self.assertEqual(sample["result"]["protocol_observation"]["events_observed"], 1)
        self.assertTrue(sample["interruption"]["after_response"])

    def test_complete_sample_recovers_reserved_ledger_and_preserves_sample_bytes(self) -> None:
        self.generated()
        rows = evaluation.read_json(self.out / "request-ledger.json")
        row = rows[0]
        row.update({"status": "reserved", "usage": None, "billing_state": "unknown"})
        evaluation.write_json(self.out / "request-ledger.json", rows)
        path = self.out / "samples" / (row["request_id"] + ".json")
        sample_bytes = path.read_bytes()
        recovered = evaluation.Ledger(self.out, self.plan)
        self.assertEqual(recovered.rows[0]["status"], "complete")
        self.assertEqual(recovered.rows[0]["usage"]["total_tokens"], 3)
        self.assertEqual(path.read_bytes(), sample_bytes)

    def test_complete_ledger_recovers_reserved_sample_without_losing_known_usage(self) -> None:
        self.generated()
        row = evaluation.read_json(self.out / "request-ledger.json")[0]
        path = self.out / "samples" / (row["request_id"] + ".json")
        sample = evaluation.read_json(path)
        sample.pop("result")
        sample["status"] = "reserved"
        sample["request_audit"].update({"dispatched": False, "dispatch_state": "prepared"})
        evaluation.write_json(path, sample)
        recovered = evaluation.Ledger(self.out, self.plan)
        restored = evaluation.read_json(path)
        self.assertEqual(recovered.rows[0]["status"], "complete")
        self.assertEqual(restored["status"], "unknown")
        self.assertEqual(restored["result"]["usage"]["total_tokens"], 3)
        self.assertEqual(restored["result"]["billing_state"], "usage_reported")
        self.assertFalse(restored["result"]["safe_to_apply"])
        self.assertEqual(restored["request_audit"], restored["result"]["request_audit"])
        self.assertTrue(restored["request_audit"]["dispatched"])

    def test_dispatch_ledger_before_sample_write_recovers_consistent_audits(self) -> None:
        self.generated()
        rows = evaluation.read_json(self.out / "request-ledger.json")
        row = rows[0]
        row.update({"status": "reserved", "usage": None, "billing_state": "unknown"})
        evaluation.write_json(self.out / "request-ledger.json", rows)
        path = self.out / "samples" / (row["request_id"] + ".json")
        sample = evaluation.read_json(path)
        sample.pop("result")
        sample["status"] = "reserved"
        sample["request_audit"].update({"dispatched": False, "dispatch_state": "prepared"})
        evaluation.write_json(path, sample)
        recovered = evaluation.Ledger(self.out, self.plan)
        restored = evaluation.read_json(path)
        self.assertEqual(recovered.rows[0]["status"], "unknown")
        self.assertEqual(restored["status"], "unknown")
        self.assertEqual(restored["request_audit"], restored["result"]["request_audit"])
        self.assertEqual(restored["request_audit"], recovered.rows[0]["request_audit"])
        self.assertTrue(restored["request_audit"]["dispatched"])
        checkpoint = evaluation.read_json(self.out / restored["reconciliation"]["checkpoint"])
        self.assertFalse(checkpoint["original_record"]["request_audit"]["dispatched"])

    def test_wrong_saved_request_hash_refuses_recovery_and_preserves_checkpoint(self) -> None:
        self.generated()
        rows = evaluation.read_json(self.out / "request-ledger.json")
        rows[0].update({"status": "reserved", "usage": None, "billing_state": "unknown"})
        evaluation.write_json(self.out / "request-ledger.json", rows)
        path = self.out / "samples" / (rows[0]["request_id"] + ".json")
        sample = evaluation.read_json(path)
        sample["result"]["request_audit"]["request_sha256"] = "f" * 64
        evaluation.write_json(path, sample)
        ledger_bytes, sample_bytes = (self.out / "request-ledger.json").read_bytes(), path.read_bytes()
        with self.assertRaisesRegex(ValueError, "身份或指纹不一致"):
            evaluation.Ledger(self.out, self.plan)
        self.assertEqual((self.out / "request-ledger.json").read_bytes(), ledger_bytes)
        self.assertEqual(path.read_bytes(), sample_bytes)
        checkpoint = evaluation.read_json(next((self.out / "reconciliations").glob("*.json")))
        self.assertEqual(checkpoint["original_record"], sample)

    def test_judge_interrupt_during_final_save_preserves_grade_and_usage(self) -> None:
        self.generated()
        evaluation.blind_pairs(self.plan, self.out)
        original = evaluation.Ledger.finish
        interrupted = []
        def fail_once(ledger, index, result, status):
            if not interrupted:
                interrupted.append(True)
                raise KeyboardInterrupt
            return original(ledger, index, result, status)
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=self.response), \
                patch.object(evaluation.Ledger, "finish", new=fail_once), \
                self.assertRaises(KeyboardInterrupt):
            evaluation.judge(self.plan, self.out, ["judge-one", "judge-two"], max_new_requests=1)
        row = evaluation.read_json(self.out / "request-ledger.json")[-1]
        record = evaluation.read_json(self.out / "grades" / (row["request_id"] + ".json"))
        self.assertEqual(row["status"], "complete")
        self.assertEqual(record["grade"]["winner"], "tie")
        self.assertEqual(record["result"]["usage"]["total_tokens"], 3)
        self.assertEqual(record["result"]["protocol_observation"]["events_observed"], 1)
        self.assertTrue(record["interruption"]["after_response"])

    def test_tampered_frozen_request_is_rejected_before_dispatch(self) -> None:
        evaluation.freeze_run(self.out, self.plan, "writer-fixture",
                              plan_path=self.plan_path, config=self.config)
        path = self.out / "frozen-writer-requests.json"
        records = evaluation.read_json(path)
        next(iter(records.values()))["payload"]["model"] = "tampered-model"
        evaluation.write_json(path, records)
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion") as transport, \
                self.assertRaises(ValueError):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture")
        transport.assert_not_called()
        self.assertFalse((self.out / "request-ledger.json").exists())

    def test_changed_endpoint_cannot_dispatch_frozen_request(self) -> None:
        evaluation.freeze_run(self.out, self.plan, "writer-fixture",
                              plan_path=self.plan_path, config=self.config)
        changed = {**self.config, "base_url": "http://127.0.0.1:10/v1"}
        with patch.object(evaluation, "provider_config", return_value=changed), \
                patch.object(evaluation, "call_chat_completion") as transport, \
                self.assertRaises(ValueError):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture")
        transport.assert_not_called()

    def test_old_snapshot_calibration_is_only_allowed_without_execution_records(self) -> None:
        evaluation.freeze_run(self.out, self.plan, "writer-fixture",
                              plan_path=self.plan_path, config=self.config)
        path = self.out / "frozen-writer-requests.json"
        records = evaluation.read_json(path)
        for record in records.values():
            record.pop("body_utf8")
        evaluation.write_json(path, records)
        run = evaluation.read_json(self.out / "run.json")
        run["writer_requests_sha256"] = evaluation.sha(records)
        evaluation.write_json(self.out / "run.json", run)
        evaluation.Ledger(self.out, self.plan).reserve("possible-paid-request", "writer", "writer-fixture")
        with self.assertRaises(ValueError):
            evaluation.freeze_run(self.out, self.plan, "writer-fixture",
                                  plan_path=self.plan_path, config=self.config)

    def test_old_snapshot_calibration_rejects_credentials_in_frozen_messages(self) -> None:
        evaluation.freeze_run(self.out, self.plan, "writer-fixture",
                              plan_path=self.plan_path, config=self.config)
        path = self.out / "frozen-writer-requests.json"
        records = evaluation.read_json(path)
        first = next(iter(records.values()))
        first["payload"]["messages"][0]["content"] += self.config["api_key"]
        first.pop("body_utf8")
        evaluation.write_json(path, records)
        run_path = self.out / "run.json"
        run = evaluation.read_json(run_path)
        run["writer_requests_sha256"] = evaluation.sha(records)
        evaluation.write_json(run_path, run)
        with self.assertRaisesRegex(ValueError, "旧评测素材意外包含凭据"):
            evaluation.freeze_run(self.out, self.plan, "writer-fixture",
                                  plan_path=self.plan_path, config=self.config)

    def test_ledger_unknown_request_still_consumes_request_budget_and_cannot_repeat(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["budget"]["max_requests"] = 1
        ledger = evaluation.Ledger(self.out, plan)
        index = ledger.reserve("unknown-request", "writer", "writer-fixture")
        ledger.finish(index, {}, "unknown")
        with self.assertRaises(ValueError):
            ledger.reserve("unknown-request", "writer", "writer-fixture")
        with self.assertRaises(ValueError):
            ledger.reserve("new-request", "writer", "writer-fixture")
        row = evaluation.read_json(ledger.path)[0]
        self.assertIsNone(row["usage"])
        self.assertEqual(row["billing_state"], "unknown")

    def test_partial_usage_cannot_bypass_unknown_failure_stop_using_legacy_billing(self) -> None:
        rows = [
            {"request_id": "partial-one", "kind": "writer", "status": "failed",
             "billing_state": "usage_reported", "usage": {}},
            {"request_id": "partial-two", "kind": "writer", "status": "failed",
             "billing_state": "usage_reported", "usage": {"completion_tokens": 3000}},
        ]
        for row in rows:
            evaluation.write_json(self.out / "samples" / (row["request_id"] + ".json"),
                                  {"id": row["request_id"], "status": "failed"})
        evaluation.write_json(self.out / "request-ledger.json", rows)
        before = (self.out / "request-ledger.json").read_bytes()
        ledger = evaluation.Ledger(self.out, self.plan)
        with self.assertRaisesRegex(ValueError, "连续模型失败"):
            ledger.reserve("must-not-dispatch", "writer", "writer-fixture")
        self.assertEqual((self.out / "request-ledger.json").read_bytes(), before)

    def test_partial_and_derived_usage_both_enforce_reported_token_stop(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["budget"]["reported_tokens_stop_threshold"] = 3
        for usage in ({"completion_tokens": 3},
                      {"prompt_tokens": 1, "completion_tokens": 2},
                      {"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 2}):
            with self.subTest(usage=usage):
                ledger = evaluation.Ledger(self.out, plan)
                ledger.rows = []
                index = ledger.reserve("single-request", "writer", "writer-fixture")
                ledger.finish(index, {"usage": usage, "billing_state": "usage_reported"}, "failed")
                with self.assertRaisesRegex(ValueError, "用量的停止阈值"):
                    ledger.reserve("must-not-dispatch", "writer", "writer-fixture")
                self.assertEqual(ledger.rows[0]["usage"], usage)
                self.assertEqual(ledger.rows[0]["billing_state"],
                                 "usage_reported" if "total_tokens" not in usage
                                 and "prompt_tokens" in usage else "unknown")

    def test_ledger_retains_inconsistent_stream_usage_high_water_mark(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["budget"]["reported_tokens_stop_threshold"] = 10
        ledger = evaluation.Ledger(self.out, plan)
        index = ledger.reserve("stream-request", "writer", "writer-fixture")
        result = {
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            "billing_state": "unknown",
            "usage_accounting": {"state": "inconsistent", "total_tokens": None,
                                 "source": "inconsistent_provider_counters",
                                 "reported_tokens_lower_bound": 14},
        }
        ledger.finish(index, result, "failed")
        row = evaluation.read_json(ledger.path)[0]
        self.assertEqual(row["usage_accounting"]["state"], "inconsistent")
        self.assertEqual(row["usage_accounting"]["reported_tokens_lower_bound"], 14)
        with self.assertRaisesRegex(ValueError, "用量的停止阈值"):
            ledger.reserve("must-not-dispatch", "writer", "writer-fixture")

    def test_historical_budget_empty_and_multiple_directories_are_explicit(self) -> None:
        empty = self.out / "empty-history"
        empty.mkdir()
        snapshot = evaluation.capture_historical_budget(self.out, [empty])
        self.assertEqual(snapshot["requests_reserved"], 0)
        self.assertEqual(snapshot["reported_tokens_lower_bound"], 0)
        self.assertEqual(snapshot["sources"][0]["path"], str(empty.resolve()))

        first = self.history_dir("first", [
            self.historical_row("history-one", "1" * 64,
                                {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}),
        ])
        second = self.history_dir("second", [
            self.historical_row("history-two", "2" * 64,
                                {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9}),
        ])
        merged = evaluation.capture_historical_budget(self.out, [first, second])
        self.assertEqual(merged["requests_reserved"], 2)
        self.assertEqual(merged["reported_tokens_lower_bound"], 19)
        self.assertEqual({item["path"] for item in merged["sources"]},
                         {str(first.resolve()), str(second.resolve())})

    def test_historical_budget_merges_repeated_request_identity_and_deduplicates_paths(self) -> None:
        request_hash = "a" * 64
        first = self.history_dir("duplicate-one", [
            self.historical_row("same-request", request_hash, None),
        ])
        second = self.history_dir("duplicate-two", [
            self.historical_row("same-request", request_hash,
                                {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}),
        ])
        snapshot = evaluation.capture_historical_budget(
            self.out, [first, second, first, second])
        self.assertEqual(snapshot["requests_reserved"], 1)
        self.assertEqual(snapshot["reported_tokens_lower_bound"], 12)
        self.assertEqual(snapshot["requests_without_complete_usage"], 0)
        self.assertEqual(snapshot["requests_with_partial_usage"], 0)

    def test_historical_budget_conflicting_complete_duplicate_stops(self) -> None:
        request_hash = "b" * 64
        first = self.history_dir("conflict-one", [
            self.historical_row("conflicting-request", request_hash,
                                {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}),
            self.historical_row("conflicting-request", request_hash,
                                {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13}),
        ])
        with self.assertRaisesRegex(ValueError, "重复请求身份对应的历史完整用量"):
            evaluation.capture_historical_budget(self.out, [first])

    def test_historical_budget_excludes_current_output_tree(self) -> None:
        root = Path(self.history_temporary.name) / "nested-history"
        old = root / "old-run"
        current = root / "current-run"
        evaluation.write_json(old / "request-ledger.json", [
            self.historical_row("old-request", "c" * 64,
                                {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}),
        ])
        evaluation.write_json(current / "request-ledger.json", [
            self.historical_row("current-request", "d" * 64,
                                {"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": 100}),
        ])
        snapshot = evaluation.capture_historical_budget(current, [root])
        self.assertEqual(snapshot["requests_reserved"], 1)
        self.assertEqual(snapshot["reported_tokens_lower_bound"], 10)

    def test_frozen_historical_budget_stops_when_ledger_changes_without_mutating_file(self) -> None:
        history = self.history_dir("frozen-history", [
            self.historical_row("frozen-request", "e" * 64,
                                {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}),
        ])
        snapshot = evaluation.capture_historical_budget(self.out, [history])
        plan = copy.deepcopy(self.plan)
        plan["historical_budget"] = snapshot
        before = (history / "request-ledger.json").read_bytes()
        evaluation.write_json(history / "request-ledger.json", [
            self.historical_row("frozen-request", "e" * 64,
                                {"prompt_tokens": 3, "completion_tokens": 8, "total_tokens": 11}),
        ])
        changed = (history / "request-ledger.json").read_bytes()
        self.assertNotEqual(before, changed)
        with self.assertRaisesRegex(ValueError, "历史账本发生变化"):
            evaluation.Ledger(self.out / "frozen-run", plan,
                              historical_budget=snapshot, history_dirs=[history])
        self.assertEqual((history / "request-ledger.json").read_bytes(), changed)

    def test_historical_request_cap_blocks_before_provider_call(self) -> None:
        rows = [
            self.historical_row(f"full-history-{index}", f"{index + 1:064x}",
                                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
            for index in range(evaluation.GLOBAL_REQUEST_CAP)
        ]
        history = self.history_dir("request-cap-history", rows)
        snapshot = evaluation.capture_historical_budget(self.out, [history])
        plan = copy.deepcopy(self.plan)
        plan["historical_budget"] = snapshot
        evaluation.write_json(self.plan_path, plan)
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion") as transport, \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(ValueError, "请求次数上限"):
            evaluation.generate(plan, self.plan_path, self.out / "request-cap-run",
                                "writer-fixture", history_dirs=[history])
        transport.assert_not_called()

    def test_historical_token_threshold_blocks_before_provider_call(self) -> None:
        history = self.history_dir("token-cap-history", [
            self.historical_row("token-cap-request", "f" * 64, {
                "prompt_tokens": evaluation.GLOBAL_REPORTED_TOKEN_THRESHOLD,
                "completion_tokens": 0,
                "total_tokens": evaluation.GLOBAL_REPORTED_TOKEN_THRESHOLD,
            }),
        ])
        snapshot = evaluation.capture_historical_budget(self.out, [history])
        plan = copy.deepcopy(self.plan)
        plan["historical_budget"] = snapshot
        evaluation.write_json(self.plan_path, plan)
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion") as transport, \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(ValueError, "用量的停止阈值"):
            evaluation.generate(plan, self.plan_path, self.out / "token-cap-run",
                                "writer-fixture", history_dirs=[history])
        transport.assert_not_called()

    def test_global_reservation_lock_blocks_second_run_before_provider_call(self) -> None:
        snapshot = evaluation.capture_historical_budget(self.out, [])
        plan = copy.deepcopy(self.plan)
        plan["historical_budget"] = snapshot
        ledger = evaluation.Ledger(
            self.out / "locked-run", plan, historical_budget=snapshot, history_dirs=[])
        with evaluation._global_budget_lock():
            with self.assertRaisesRegex(ValueError, "另一轮文学评测正在预留请求"):
                ledger.reserve("lock-blocked-request", "writer", "writer-fixture")

    def test_global_reservation_under_history_root_counts_as_another_new_run(self) -> None:
        history_root = Path(self.history_temporary.name) / "audits"
        history_root.mkdir()
        snapshot = evaluation.capture_historical_budget(self.out, [history_root])
        snapshot["request_cap"] = 1
        snapshot["reported_tokens_stop_threshold"] = 100
        plan = copy.deepcopy(self.plan)
        plan["historical_budget"] = snapshot
        other_run = history_root / "new-run"
        evaluation.write_json(evaluation.GLOBAL_RESERVATION_PATH, [{
            "run_dir": str(other_run),
            "request_id": "other-new-request",
            "usage": None,
            "usage_accounting": {
                "state": "unknown", "total_tokens": None,
                "reported_tokens_lower_bound": 0, "source": "unknown",
            },
        }])
        ledger = evaluation.Ledger(
            self.out / "current-run", plan, historical_budget=snapshot,
            history_dirs=[history_root])
        with self.assertRaisesRegex(ValueError, "请求次数上限"):
            ledger.reserve("current-request", "writer", "writer-fixture")

    def test_global_reservation_already_in_frozen_history_is_not_counted_twice(self) -> None:
        history_root = Path(self.history_temporary.name) / "audits-with-complete-run"
        other_run = history_root / "new-run"
        history_row = self.historical_row(
            "historical-request", "1" * 64,
            {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10})
        evaluation.write_json(other_run / "request-ledger.json", [history_row])
        snapshot = evaluation.capture_historical_budget(self.out, [history_root])
        snapshot["request_cap"] = 2
        plan = copy.deepcopy(self.plan)
        plan["historical_budget"] = snapshot
        evaluation.write_json(evaluation.GLOBAL_RESERVATION_PATH, [{
            "run_dir": str(other_run),
            "request_id": "historical-request",
            "usage": history_row["usage"],
            "usage_accounting": history_row.get("usage_accounting"),
        }])
        ledger = evaluation.Ledger(
            self.out / "current-run", plan, historical_budget=snapshot,
            history_dirs=[history_root])
        # The historical row consumes one of two global request slots.  Its
        # matching global registry row must not consume the same slot again.
        index = ledger.reserve("current-request", "writer", "writer-fixture")
        self.assertEqual(index, 0)

    def test_blinding_is_deterministic_and_does_not_expose_arm_or_model_ids(self) -> None:
        self.generated()
        first = evaluation.blind_pairs(self.plan, self.out)
        second = evaluation.blind_pairs(self.plan, self.out)
        self.assertEqual(first, second)
        self.assertEqual(len(first["pairs"]), len(self.plan["cases"]))
        for pair in first["pairs"]:
            self.assertNotIn("arm_id", pair)
            self.assertNotIn("model", pair)
        for word in ("writer-fixture", "moyu-prompt", "plain-context"):
            self.assertNotIn(word, (self.out / "blind-review.md").read_text())
        self.assertTrue((self.out / "blind-key-private.json").exists())

    def test_self_grading_or_single_judge_is_rejected(self) -> None:
        self.generated()
        evaluation.blind_pairs(self.plan, self.out)
        for models in (["judge-one"], ["judge-one", "judge-one"], ["writer-fixture", "judge-one"]):
            with self.assertRaises(ValueError):
                evaluation.judge(self.plan, self.out, models)

    def test_same_grades_in_new_runs_have_distinct_external_request_ids_and_resume_skips_them(self) -> None:
        self.generated()
        second_out = self.out / "second-run"
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=self.response), \
                contextlib.redirect_stdout(io.StringIO()):
            evaluation.generate(self.plan, self.plan_path, second_out, "writer-fixture")
            for out in (self.out, second_out):
                evaluation.blind_pairs(self.plan, out)
                evaluation.judge(self.plan, out, ["judge-one", "judge-two"], max_new_requests=2)
            first_count = len(self.calls)
            evaluation.judge(self.plan, self.out, ["judge-one", "judge-two"], max_new_requests=2)
        judge_calls = [call for call in self.calls if call["options"].get("task") == "literary_judge"]
        first_ids = {call["options"]["request_id"] for call in judge_calls[:2]}
        self.assertTrue(first_ids.isdisjoint(call["options"]["request_id"] for call in judge_calls[2:4]))
        self.assertTrue(first_ids.isdisjoint(call["options"]["request_id"] for call in judge_calls[4:]))
        self.assertEqual(len(self.calls), first_count + 2)
        second_grades = sorted((second_out / "grades").glob("*.json"))
        self.assertTrue({path.name for path in second_grades}.issubset(
            path.name for path in (self.out / "grades").glob("*.json")))
        for second_path in second_grades:
            first = evaluation.read_json(self.out / "grades" / second_path.name)
            second = evaluation.read_json(second_path)
            self.assertEqual(first["id"], second["id"])
            self.assertNotEqual(first["request_audit"]["request_id"], second["request_audit"]["request_id"])

    def test_legacy_run_without_identity_still_reports_but_cannot_dispatch_judges(self) -> None:
        self.generated()
        evaluation.blind_pairs(self.plan, self.out)
        run_path = self.out / "run.json"
        run = evaluation.read_json(run_path)
        run.pop("run_id")
        evaluation.write_json(run_path, run)
        paths = [run_path, self.out / "request-ledger.json"]
        before = [path.read_bytes() for path in paths]
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion") as transport, \
                self.assertRaisesRegex(ValueError, "独立运行身份"):
            evaluation.judge(self.plan, self.out, ["judge-one", "judge-two"])
        transport.assert_not_called()
        self.assertFalse((self.out / "grades").exists())
        self.assertFalse(evaluation.report(self.plan, self.out)["first_tier_proven"])
        self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_invalid_quote_is_rejected_and_cannot_count_toward_complete_evidence(self) -> None:
        self.generated()
        def invalid(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            pair = json.loads(messages[1]["content"])
            result["structured"]["hard_violations"] = [{
                "candidate": "A", "rule_id": pair["rules"][0]["id"],
                "quote": "这句话并不在任何候选里。", "explanation": "虚构证据。"}]
            return result
        self.graded(invalid)
        report = evaluation.report(self.plan, self.out)
        self.assertEqual(report["judging"]["valid_grades"], 0)
        self.assertFalse(report["pilot_evidence_complete"])
        self.assertFalse(report["first_tier_proven"])
        self.assertGreater(report["judging"]["invalid_or_incomplete_grades"], 0)

    def test_no_grades_cannot_be_claimed_as_complete_or_first_tier(self) -> None:
        self.generated()
        evaluation.blind_pairs(self.plan, self.out)
        report = evaluation.report(self.plan, self.out)
        self.assertFalse(report["pilot_evidence_complete"])
        self.assertFalse(report["acceptance_gates"]["all_planned_pairs_graded"])
        self.assertFalse(report["acceptance_gates"]["minimum_genres"])
        self.assertEqual(report["judging"]["minimum_required_grades"], 24)
        self.assertEqual(report["reviewed_genres"], [])
        self.assertFalse(report["first_tier_proven"])

    def test_thirty_planned_stories_without_candidates_do_not_pass_reviewed_story_gate(self) -> None:
        self.story_coverage_plan()
        evaluation.write_json(self.out / "run.json",
                              {"plan_sha256": evaluation.sha(self.plan),
                               "writer_model": "writer-fixture"})
        evaluation.write_json(self.out / "request-ledger.json", [])
        evaluation.blind_pairs(self.plan, self.out)
        with patch.object(evaluation, "call_chat_completion") as transport:
            report = evaluation.report(self.plan, self.out)
        transport.assert_not_called()
        self.assertFalse(report["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        self.assertEqual(report["distinct_story_coverage"]["planned"], 30)
        self.assertEqual(report["distinct_story_coverage"]["observed"], 0)
        self.assertEqual(report["distinct_story_coverage"]["output_eligible"], 0)
        self.assertEqual(report["distinct_story_coverage"]["reviewed_by_comparator"], {"baseline": 0})
        self.assertFalse(report["first_tier_proven"])

    def test_thirty_generated_stories_without_grades_preserve_observed_and_reviewed_scope(self) -> None:
        self.story_coverage_plan()
        self.generated()
        evaluation.blind_pairs(self.plan, self.out)
        report = evaluation.report(self.plan, self.out)
        self.assertFalse(report["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        self.assertEqual(report["distinct_story_coverage"]["planned"], 30)
        self.assertEqual(report["distinct_story_coverage"]["observed"], 30)
        self.assertEqual(report["distinct_story_coverage"]["output_eligible"], 30)
        self.assertEqual(report["distinct_story_coverage"]["reviewed_by_comparator"], {"baseline": 0})
        self.assertIn("实际返回独立故事 30 个", (self.out / "report.md").read_text())
        self.assertIn("完整评审的独立故事：baseline 0 个", (self.out / "report.md").read_text())

    def test_reviewed_story_gate_requires_both_orders_for_each_judge_and_keeps_thirty_threshold(self) -> None:
        self.story_coverage_plan()
        self.generated()
        self.graded()
        complete = evaluation.report(self.plan, self.out)
        self.assertTrue(complete["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        self.assertEqual(complete["distinct_story_coverage"]["reviewed_by_comparator"], {"baseline": 30})
        self.assertEqual(complete["distinct_story_coverage"]["minimum_required_per_comparator"], 30)
        self.assertFalse(complete["first_tier_proven"])
        pair = next(pair for pair in evaluation.read_json(self.out / "blind-pairs.json")["pairs"]
                    if pair["case_id"] == self.plan["cases"][-1]["id"])
        for path in (self.out / "grades").glob("*.json"):
            observation = evaluation.read_json(path)
            if observation["pair_id"] == pair["id"] and observation["judge_model"] == "judge-two":
                if observation["order"] == "BA":
                    observation["status"] = "incomplete"
                    evaluation.write_json(path, observation)
                    break
        report = evaluation.report(self.plan, self.out)
        self.assertEqual(report["judging"]["valid_grades"], 119)
        self.assertEqual(report["distinct_story_coverage"]["reviewed_by_comparator"], {"baseline": 29})
        self.assertEqual(report["distinct_story_coverage"]["minimum_required_per_comparator"], 30)
        self.assertFalse(report["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        self.assertFalse(report["first_tier_proven"])

    def test_saved_sample_task_mismatch_cannot_count_as_reviewed_story(self) -> None:
        self.assert_mismatched_sample_identity_is_excluded("task", "summary")

    def test_saved_sample_genre_mismatch_cannot_count_as_reviewed_story(self) -> None:
        self.assert_mismatched_sample_identity_is_excluded("genre", "与固定题目不一致的题材")

    def test_duplicate_story_ids_repetitions_and_grade_records_do_not_inflate_story_coverage(self) -> None:
        self.story_coverage_plan(repetitions=2, duplicate_stories=True)
        self.generated()
        self.graded()
        original = next((self.out / "grades").glob("*.json"))
        duplicate = self.out / "grades" / "duplicate-observation.json"
        evaluation.write_json(duplicate, evaluation.read_json(original))
        report = evaluation.report(self.plan, self.out)
        self.assertEqual(report["judging"]["valid_grades"], 240)
        self.assertEqual(report["judging"]["invalid_or_incomplete_grades"], 1)
        comparator = report["judging"]["paired_preference"]["baseline"]
        self.assertEqual(comparator["paired_samples_with_complete_two_order_judges"], 60)
        self.assertEqual(comparator["distinct_stories"], 15)
        self.assertEqual(report["distinct_story_coverage"]["planned"], 15)
        self.assertEqual(report["distinct_story_coverage"]["observed"], 15)
        self.assertEqual(report["distinct_story_coverage"]["reviewed_by_comparator"], {"baseline": 15})
        self.assertFalse(report["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])

    def test_reviewed_story_gate_requires_thirty_stories_for_every_comparator(self) -> None:
        self.story_coverage_plan(second_comparator=True)
        self.generated()
        self.graded()
        pairs = evaluation.read_json(self.out / "blind-pairs.json")["pairs"]
        mappings = evaluation.read_json(self.out / "blind-key-private.json")
        last_case = self.plan["cases"][-1]["id"]
        pair_ids = {pair["id"] for pair in pairs if pair["case_id"] == last_case}
        missing_pair = next(mapping["id"] for mapping in mappings
                            if mapping["id"] in pair_ids and mapping["comparator"] == "baseline-two")
        for path in (self.out / "grades").glob("*.json"):
            observation = evaluation.read_json(path)
            if observation["pair_id"] == missing_pair:
                observation["status"] = "incomplete"
                evaluation.write_json(path, observation)
        report = evaluation.report(self.plan, self.out)
        self.assertEqual(report["distinct_story_coverage"]["planned"], 30)
        self.assertEqual(report["distinct_story_coverage"]["reviewed_by_comparator"],
                         {"baseline": 30, "baseline-two": 29})
        self.assertFalse(report["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        self.assertFalse(report["first_tier_proven"])

    def test_returned_judge_aliases_cannot_satisfy_distinct_model_story_coverage(self) -> None:
        self.story_coverage_plan()
        self.generated()
        def aliased(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            result["returned_model"] = "one-actual-judge"
            return result
        self.graded(aliased)
        report = evaluation.report(self.plan, self.out)
        self.assertEqual(report["judging"]["valid_grades"], 120)
        self.assertEqual(report["judging"]["distinct_judge_model_ids"], ["judge-one", "judge-two"])
        self.assertEqual(report["judging"]["distinct_returned_judge_models"], ["one-actual-judge"])
        self.assertEqual(report["distinct_story_coverage"]["reviewed_by_comparator"], {"baseline": 0})
        self.assertFalse(report["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        self.assertFalse(report["first_tier_proven"])

    def test_failed_samples_report_observed_identity_and_partial_usage_without_claiming_coverage(self) -> None:
        def failed(config, messages, **kwargs):
            result = self.response(config, messages, **kwargs)
            result.update({"text": "", "safe_to_apply": False, "completion_state": "failed",
                           "usage": {"completion_tokens": 3000}})
            return result
        with patch.object(evaluation, "provider_config", side_effect=self.config_for), \
                patch.object(evaluation, "call_chat_completion", side_effect=failed), \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(ValueError, "连续模型失败"):
            evaluation.generate(self.plan, self.plan_path, self.out, "writer-fixture",
                                max_new_requests=2)
        evaluation.blind_pairs(self.plan, self.out)
        report = evaluation.report(self.plan, self.out)
        self.assertEqual(len(report["planned_genres"]), 4)
        self.assertEqual(report["observed_genres"], ["悬疑"])
        self.assertEqual(report["eligible_genres"], [])
        self.assertEqual(report["reviewed_genres"], [])
        self.assertEqual(report["generation"]["requested_models"], [])
        self.assertEqual(report["generation"]["observed_requested_models"], ["writer-fixture"])
        self.assertEqual(report["generation"]["observed_returned_models"], ["writer-fixture"])
        self.assertEqual(len(report["generation"]["observed_model_identities"]), 2)
        self.assertEqual(report["judging"]["valid_grades"], 0)
        self.assertEqual(report["judging"]["minimum_required_grades"], 24)
        self.assertEqual(report["usage"]["reported_tokens"], 0)
        self.assertEqual(report["usage"]["reported_tokens_lower_bound"], 6000)
        self.assertEqual(report["usage"]["requests_without_usage"], 2)
        self.assertEqual(report["usage"]["requests_with_partial_usage"], 2)
        self.assertFalse(report["acceptance_gates"]["minimum_genres"])
        self.assertFalse(report["pilot_evidence_complete"])
        self.assertIn("有效评审 0/24", (self.out / "report.md").read_text())
        self.assertIn("实际返回题材 1 类", (self.out / "report.md").read_text())

    def test_single_judge_plan_is_rejected_by_direct_judge_and_report_calls(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["judging"]["minimum_distinct_judge_models"] = 1
        with patch.object(evaluation, "call_chat_completion") as transport:
            with self.assertRaisesRegex(ValueError, "两种不同"):
                evaluation.judge(plan, self.out, ["judge-one"])
            with self.assertRaisesRegex(ValueError, "两种不同"):
                evaluation.report(plan, self.out)
        transport.assert_not_called()
        self.assertFalse((self.out / "request-ledger.json").exists())

    def test_complete_two_order_pilot_still_requires_actual_author_efficiency(self) -> None:
        self.generated()
        self.graded()
        report = evaluation.report(self.plan, self.out)
        self.assertEqual(len(self.calls), 36)
        self.assertEqual(report["judging"]["valid_grades"], 24)
        self.assertTrue(report["pilot_evidence_complete"])
        self.assertFalse(report["first_tier_proven"])
        self.assertEqual(report["author_efficiency"]["status"], "unmeasured")
        self.assertIsNone(report["usage"]["currency_cost"])
        self.assertFalse(report["acceptance_gates"]["sufficient_distinct_stories_for_tier_claim"])
        comparator = report["judging"]["paired_preference"]["baseline"]
        self.assertEqual(comparator["distinct_stories"], 6)
        self.assertEqual(comparator["ties"], 6)
        self.assertIsNone(comparator["decisive_preference_wilson_95"])

    def test_report_revalidates_evidence_in_saved_complete_grades(self) -> None:
        self.generated()
        self.graded()
        for path in (self.out / "grades").glob("*.json"):
            observation = evaluation.read_json(path)
            pair = next(item for item in evaluation.read_json(self.out / "blind-pairs.json")["pairs"]
                        if item["id"] == observation["pair_id"])
            observation["grade"]["hard_violations"] = [{
                "candidate": "A", "rule_id": pair["rules"][0]["id"],
                "quote": "此句在候选中不存在。", "explanation": "无效的保存后证据。"}]
            evaluation.write_json(path, observation)
        result = evaluation.report(self.plan, self.out)
        self.assertFalse(result["pilot_evidence_complete"])
        self.assertEqual(result["judging"]["valid_grades"], 0)
        self.assertEqual(result["judging"]["pairs_with_two_complete_judges"], 0)

    def test_grade_count_cannot_replace_missing_pair_coverage(self) -> None:
        self.generated()
        self.graded()
        pairs = evaluation.read_json(self.out / "blind-pairs.json")["pairs"]
        for path in (self.out / "grades").glob("*.json"):
            observation = evaluation.read_json(path)
            if observation["pair_id"] == pairs[0]["id"]:
                observation["pair_id"] = pairs[1]["id"]
                evaluation.write_json(path, observation)
        result = evaluation.report(self.plan, self.out)
        self.assertFalse(result["acceptance_gates"]["all_planned_pairs_graded"])
        self.assertFalse(result["pilot_evidence_complete"])
        self.assertLess(result["judging"]["pairs_with_two_complete_judges"], len(pairs))

    def test_record_human_ratings_imports_desktop_export_without_provider_calls(self) -> None:
        self.generated()
        blind = evaluation.blind_pairs(self.plan, self.out)
        pair = blind["pairs"][0]
        source = self.out / "desktop-author-ratings.json"
        evaluation.write_json(source, {
            "source": "desktop-chrome",
            "desktop_run_id": "desktop-eval-fixture",
            "ratings": [{
                "pair_id": pair["id"], "reviewer_id": "author-01", "winner": "A",
                "adoptable_A": True, "adoptable_B": False,
                "edit_seconds_A": 12.5, "edit_seconds_B": 20,
                "hard_violations": [], "notes": "离线桌面端 fixture。",
            }],
        })
        before = len(self.calls)
        with patch.object(evaluation, "call_chat_completion") as transport:
            payload = evaluation.record_human_ratings(self.plan, self.out, source)
        transport.assert_not_called()
        self.assertEqual(len(self.calls), before)
        self.assertEqual(payload["protocol"], evaluation.HUMAN_RATINGS_VERSION)
        saved = evaluation.read_json(self.out / "human-ratings.json")
        self.assertEqual(saved["plan_sha256"], evaluation.sha(self.plan))
        self.assertEqual(saved["source"], "desktop-chrome")
        self.assertEqual(len(saved["ratings"]), 1)
        report = evaluation.report(self.plan, self.out)
        author = report["author_efficiency"]
        self.assertEqual(author["status"], "measured")
        self.assertEqual(author["ratings"], 1)
        self.assertEqual(author["reviewers"], 1)
        self.assertEqual(author["adoption_rate"], 0.5)
        self.assertEqual(author["edit_seconds"]["median"], 16.25)
        self.assertEqual(author["edit_seconds"]["p95"], 19.625)
        self.assertTrue(report["acceptance_gates"]["actual_author_efficiency_measured"])
        self.assertFalse(report["pilot_quality_passed"])
        self.assertIn("作者实际采纳率 50.0%", (self.out / "report.md").read_text())

    def test_record_human_ratings_rejects_unknown_duplicate_and_conflicting_rows(self) -> None:
        self.generated()
        blind = evaluation.blind_pairs(self.plan, self.out)
        pair = blind["pairs"][0]
        base = {
            "pair_id": pair["id"], "reviewer_id": "author-01", "winner": "tie",
            "adoptable_A": True, "adoptable_B": None,
            "edit_seconds_A": 3, "edit_seconds_B": None,
            "hard_violations": [], "notes": "",
        }
        duplicate = self.out / "duplicate-ratings.json"
        evaluation.write_json(duplicate, [base, dict(base)])
        with self.assertRaisesRegex(ValueError, "不能重复"):
            evaluation.record_human_ratings(self.plan, self.out, duplicate)
        self.assertFalse((self.out / "human-ratings.json").exists())

        unknown = self.out / "unknown-ratings.json"
        evaluation.write_json(unknown, [{**base, "pair_id": "pair_missing"}])
        with self.assertRaisesRegex(ValueError, "pair_id"):
            evaluation.record_human_ratings(self.plan, self.out, unknown)

        first = self.out / "first-ratings.json"
        evaluation.write_json(first, [base])
        evaluation.record_human_ratings(self.plan, self.out, first)
        before = (self.out / "human-ratings.json").read_bytes()
        conflict = self.out / "conflict-ratings.json"
        evaluation.write_json(conflict, [{**base, "winner": "A"}])
        with self.assertRaisesRegex(ValueError, "不同记录"):
            evaluation.record_human_ratings(self.plan, self.out, conflict)
        self.assertEqual((self.out / "human-ratings.json").read_bytes(), before)

    def test_record_human_ratings_cli_is_offline_and_merges_idempotently(self) -> None:
        self.generated()
        blind = evaluation.blind_pairs(self.plan, self.out)
        rows = [{
            "pair_id": blind["pairs"][0]["id"], "reviewer_id": "author-01",
            "winner": "A", "adoptable_A": True, "adoptable_B": False,
            "edit_seconds_A": 4, "edit_seconds_B": 5,
            "hard_violations": [], "notes": "",
        }]
        source = self.out / "cli-ratings.json"
        evaluation.write_json(source, {"ratings": rows})
        with patch.object(evaluation, "call_chat_completion") as transport, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            status = evaluation.main([
                "record-human", "--output-dir", str(self.out),
                "--plan", str(self.plan_path), "--ratings", str(source),
            ])
        transport.assert_not_called()
        self.assertEqual(status, 0)
        self.assertIn('"ratings": 1', output.getvalue())
        # Re-importing the exact export is idempotent and does not duplicate rows.
        with patch.object(evaluation, "call_chat_completion") as transport:
            evaluation.record_human_ratings(self.plan, self.out, source)
        transport.assert_not_called()
        self.assertEqual(len(evaluation.read_json(self.out / "human-ratings.json")["ratings"]), 1)

    def test_record_human_ratings_rejects_changed_blind_text_and_saved_snapshot(self) -> None:
        self.generated()
        blind = evaluation.blind_pairs(self.plan, self.out)
        pair = blind["pairs"][0]
        source = self.out / "snapshot-ratings.json"
        evaluation.write_json(source, [{
            "pair_id": pair["id"], "reviewer_id": "author-01",
            "adoptable_A": True, "adoptable_B": False,
            "edit_seconds_A": 4, "edit_seconds_B": 5,
        }])
        original = copy.deepcopy(blind)
        blind["pairs"][0]["A"] += "匿名文本已变化。"
        evaluation.write_json(self.out / "blind-pairs.json", blind)
        with self.assertRaisesRegex(ValueError, "文本已变化"):
            evaluation.record_human_ratings(self.plan, self.out, source)
        self.assertFalse((self.out / "human-ratings.json").exists())

        evaluation.write_json(self.out / "blind-pairs.json", original)
        evaluation.record_human_ratings(self.plan, self.out, source)
        evaluation.report(self.plan, self.out)
        rating_before = (self.out / "human-ratings.json").read_bytes()
        report_before = (self.out / "report.json").read_bytes()
        mapping = next(item for item in evaluation.read_json(self.out / "blind-key-private.json")
                       if item["id"] == pair["id"])
        sample_path = self.out / "samples" / (mapping["A_sample"] + ".json")
        sample = evaluation.read_json(sample_path)
        sample["result"]["text"] += "新"
        evaluation.write_json(sample_path, sample)
        original["pairs"][0]["A"] = sample["result"]["text"]
        evaluation.write_json(self.out / "blind-pairs.json", original)
        with self.assertRaisesRegex(ValueError, "快照已变化"):
            evaluation.report(self.plan, self.out)
        self.assertEqual((self.out / "human-ratings.json").read_bytes(), rating_before)
        self.assertEqual((self.out / "report.json").read_bytes(), report_before)

    def test_record_human_ratings_invalid_field_types_do_not_save_partial_records(self) -> None:
        self.generated()
        blind = evaluation.blind_pairs(self.plan, self.out)
        base = {"pair_id": blind["pairs"][0]["id"], "reviewer_id": "author-01",
                "adoptable_A": True}
        source = self.out / "invalid-types-ratings.json"
        for changes in ({"winner": []}, {"winner": {}}, {"edit_seconds_A": True},
                        {"edit_seconds_A": -1}, {"edit_seconds_A": 10 ** 400},
                        {"adoptable_A": 1}, {"hard_violations": {}}):
            with self.subTest(changes=changes):
                evaluation.write_json(source, [{**base, **changes}])
                with self.assertRaises(ValueError):
                    evaluation.record_human_ratings(self.plan, self.out, source)
                self.assertFalse((self.out / "human-ratings.json").exists())

    def test_human_efficiency_requires_both_adoption_and_edit_time(self) -> None:
        self.generated()
        blind = evaluation.blind_pairs(self.plan, self.out)
        source = self.out / "partial-ratings.json"
        evaluation.write_json(source, [{
            "pair_id": blind["pairs"][0]["id"], "reviewer_id": "author-01",
            "adoptable_A": True,
        }])
        evaluation.record_human_ratings(self.plan, self.out, source)
        result = evaluation.report(self.plan, self.out)
        self.assertEqual(result["author_efficiency"]["status"], "partial")
        self.assertEqual(result["author_efficiency"]["adoption_rate"], 1)
        self.assertIsNone(result["author_efficiency"]["edit_seconds"])
        self.assertFalse(result["acceptance_gates"]["actual_author_efficiency_measured"])
        self.assertFalse(result["first_tier_proven"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
