#!/usr/bin/env python3
"""Free message-shape fixtures; uses temporary files and simulated responses."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "message_shape_diagnostic", Path(__file__).with_name("execute_diagnostic.py"))
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)
provider = diagnostic.provider
evaluation = diagnostic.evaluation


class NoCredentialReads(dict):
    def get(self, *_args, **_kwargs):
        raise AssertionError("Offline work must not read a credential.")


class Response:
    status = 200

    def getheader(self, name, default=""):
        return "application/json; charset=utf-8" if name == "Content-Type" else default


class FreeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="moyu-message-shape-offline-")
        self.out = Path(self.temporary.name)
        self.calls = []
        self.live_history_paths = diagnostic.history_paths

        def preregistered_history_paths(out):
            # Fixtures retain the old baseline; live runs add OUT's own ledger.
            return [path for path in self.live_history_paths(out)
                    if path.parent.resolve() != diagnostic.OUT.resolve()]

        self.history_patch = patch.object(
            diagnostic, "history_paths", side_effect=preregistered_history_paths)
        self.history_patch.start()
        self.addCleanup(self.history_patch.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self):
        with patch.object(diagnostic.os, "environ", NoCredentialReads()), \
                patch.object(provider, "call_chat_completion",
                             side_effect=AssertionError("No real call allowed")):
            diagnostic.prepare(self.out)

    @staticmethod
    def text():
        return "林岚握住从左靴内侧取出的月牙钥匙，沿着旧码头台阶走出仓库。" * 24

    def responder(self, config, messages, **options):
        self.calls.append({"messages": copy.deepcopy(messages),
                           "request_id": options["request_id"], "stream": options["stream"]})
        prepared = provider.prepare_chat_request(
            config, messages, **{key: options[key] for key in
                ("task", "max_tokens", "stream", "parameters", "request_id")})
        audit = {**prepared["request_audit"], "dispatched": True,
                 "dispatch_state": "dispatch_intent_recorded"}
        options["on_dispatch"](audit)
        diagnostic.http.client.HTTPConnection.getresponse(object())
        accumulator = provider._Accumulator("continue", "text", config["api_key"],
                                            None, None, thinking_disabled=True)
        accumulator.payload(self.reply, sse=False)
        result = accumulator.result(sse=False, finalize=True)
        result.update(request_audit=audit, billing_state="usage_reported")
        return result

    def run_first(self, *, reasoning=None, reasoning_field="reasoning_content",
                  text=None, usage=True, finish="stop"):
        self.reply = {
            "model": "glm-5.1", "choices": [{"index": 0, "message": {
                "role": "assistant", "content": self.text() if text is None else text},
                "finish_reason": finish}],
        }
        if reasoning is not None:
            self.reply["choices"][0]["message"][reasoning_field] = reasoning
        if usage:
            self.reply["usage"] = {"prompt_tokens": 12, "completion_tokens": 34,
                                    "total_tokens": 46}
        with patch.object(diagnostic.os, "environ",
                          {"MOYU_AI_API_KEY": "offline-fixture-private-token"}), \
                patch.object(provider, "call_chat_completion", side_effect=self.responder), \
                patch.object(diagnostic.http.client.HTTPConnection, "getresponse",
                             return_value=Response()):
            return diagnostic.execute("merged_nonstream", self.out)

    def make_review(self, passed=True):
        record = evaluation.read_json(self.out / "samples/merged_nonstream.json")
        review = {
            "record_id": record["id"], "reviewer": "Offline fixture only",
            "text_sha256": diagnostic.digest_bytes(record["result"]["text"].encode()),
            "checks": {name: {"passed": passed, "rationale": "Fixture gate structure.",
                "evidence": [{"quote": "林岚", "note": "This is a simulated citation."}]}
                for name in diagnostic.FACTUAL_CHECKS},
        }
        path = self.out / "factual-review-fixture.json"
        evaluation.write_json(path, review)
        return path, review

    def test_merge_preserves_contents_and_never_crosses_another_role(self):
        messages = [{"role": "system", "content": "系统"},
                    {"role": "user", "content": "甲"},
                    {"role": "user", "content": "乙"},
                    {"role": "assistant", "content": "回复"},
                    {"role": "user", "content": "丙"}]
        unchanged = copy.deepcopy(messages)
        merged = diagnostic.merge_adjacent_users(messages)
        self.assertEqual(messages, unchanged)
        self.assertEqual(merged[0], messages[0])
        self.assertEqual(merged[1], {"role": "user", "content": "甲\n\n乙"})
        self.assertEqual(merged[2:], messages[3:])

    def test_named_or_non_string_message_is_rejected(self):
        for message in ({"role": "user", "content": "甲", "name": "different"},
                        {"role": "user", "content": []}):
            with self.assertRaises(ValueError):
                diagnostic.merge_adjacent_users([message])

    def test_actual_frozen_input_has_only_the_25_byte_shape_change(self):
        frozen = diagnostic.inputs()
        self.assertEqual(frozen["request_sha256"],
            "50f572b77290baefbc0dabcb673396d1be24cc35ff5a061582dab5d3f32b484b")
        self.assertEqual(frozen["messages_sha256"],
            "53b1e8bdb06879013e769035b6fed68d9c2e3201038cfc3121249e01af68091c")
        difference = frozen["difference"]
        self.assertEqual(difference["body_bytes_before"], 7367)
        self.assertEqual(difference["body_bytes_after"], 7342)
        self.assertEqual(difference["body_bytes_delta"], -25)
        self.assertEqual(difference["removed_message_boundary_json_bytes"], 29)
        self.assertEqual(difference["separator_utf8_hex"], "0a0a")
        self.assertEqual(difference["content_bytes_delta"], 2)
        self.assertEqual(difference["content_bytes_after"] -
                         difference["content_bytes_before"], 2)
        self.assertTrue(difference["system_is_identical"])
        self.assertTrue(difference["candidate_json_is_identical_complete_suffix"])
        self.assertTrue(difference["non_message_parameters_are_identical"])
        self.assertEqual(difference["candidate_characters"], 868)

    def test_preview_and_prepare_never_read_credentials_or_send(self):
        with patch.object(diagnostic.os, "environ", NoCredentialReads()), \
                patch.object(provider, "call_chat_completion",
                             side_effect=AssertionError("No real call allowed")):
            preview = diagnostic.preview(self.out)
            result = diagnostic.prepare(self.out)
        self.assertEqual(preview["cumulative_before"]["requests_reserved"], 34)
        self.assertEqual(preview["cumulative_before"]["complete_tokens"], 63876)
        self.assertEqual(preview["cumulative_before"]["missing_complete_usage"], 10)
        self.assertEqual(result["sent_requests"], 0)
        self.assertFalse((self.out / "request-ledger.json").exists())
        self.assertEqual(
            (self.out / "frozen-request.body.json").read_bytes(),
            evaluation.read_json(self.out / "frozen-request.json")["body_utf8"].encode())

    def test_future_live_ledger_keeps_free_fixtures_repeatable_and_live_totals_current(self):
        actual_ledger = diagnostic.OUT / "request-ledger.json"
        original_paths = self.live_history_paths
        original_exists = Path.exists
        original_read_json = evaluation.read_json
        future_rows = [{"request_id": "simulated-live-first", "usage": {
            "prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}}]

        def live_paths_with_new_ledger(out):
            paths = list(original_paths(out))
            if actual_ledger not in paths and out.resolve() != diagnostic.OUT.resolve():
                paths.append(actual_ledger)
            return paths

        def exists_with_new_ledger(path):
            return True if path.resolve() == actual_ledger.resolve() else original_exists(path)

        def read_with_new_ledger(path):
            if path.resolve() == actual_ledger.resolve():
                return copy.deepcopy(future_rows)
            return original_read_json(path)

        with patch.object(self, "live_history_paths", side_effect=live_paths_with_new_ledger), \
                patch.object(Path, "exists", new=exists_with_new_ledger), \
                patch.object(evaluation, "read_json", side_effect=read_with_new_ledger), \
                patch.object(diagnostic.os, "environ", NoCredentialReads()), \
                patch.object(provider, "call_chat_completion",
                             side_effect=AssertionError("No real call allowed")):
            baseline_paths = diagnostic.history_paths(self.out)
            self.assertNotIn(actual_ledger, baseline_paths)
            self.assertEqual(sum(path.name == "request-ledger.json"
                                 for path in baseline_paths), 14)
            self.assertEqual(sum(path.name.startswith("provider-shape-")
                                 for path in baseline_paths), 1)
            for number in range(2):
                rerun = self.out / f"rerun-{number}"
                preview = diagnostic.preview(rerun)
                prepared = diagnostic.prepare(rerun)
                self.assertEqual(preview["cumulative_before"]["requests_reserved"], 34)
                self.assertEqual(preview["cumulative_before"]["known_lower_bound"], 63876)
                self.assertEqual(preview["cumulative_before"]["missing_complete_usage"], 10)
                self.assertEqual(prepared["sent_requests"], 0)
                self.assertFalse((rerun / "request-ledger.json").exists())
            current = diagnostic.cumulative(diagnostic.OUT)
            self.assertEqual(current["requests_reserved"], 35)
            self.assertEqual(current["known_lower_bound"], 63876 + 46)
            self.assertEqual(current["missing_complete_usage"], 10)
            with self.assertRaises(ValueError):
                diagnostic.preview(diagnostic.OUT)

    def test_prepare_cannot_overwrite_an_independent_run(self):
        self.prepare()
        before = (self.out / "plan.json").read_bytes()
        with self.assertRaises(ValueError):
            diagnostic.prepare(self.out)
        self.assertEqual((self.out / "plan.json").read_bytes(), before)

    def test_cumulative_adds_own_unknown_row_instead_of_starting_again(self):
        evaluation.write_json(self.out / "request-ledger.json", [
            {"request_id": "fixture", "usage": None},
            {"request_id": "fixture-complete", "usage": {
                "prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}},
        ])
        current = diagnostic.cumulative(self.out)
        self.assertEqual(current["requests_reserved"], 36)
        self.assertEqual(current["known_lower_bound"], 63876 + 46)
        self.assertEqual(current["missing_complete_usage"], 11)
        with self.assertRaises(ValueError):
            diagnostic.preview(self.out)

    def test_observer_never_retains_reasoning_text(self):
        observer = diagnostic.Observer()
        observer.observe({"model": "glm-5.1", "choices": [{"message": {
            "content": "正文", "reasoning_content": "私密思考不应保存"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}, False)
        saved = observer.saved_stats({"text": "正文"}, "")
        self.assertEqual(saved["reasoning_nonempty_events"], 1)
        self.assertEqual(saved["reasoning_characters"], len("私密思考不应保存"))
        self.assertNotIn("私密思考不应保存", json.dumps(saved, ensure_ascii=False))
        self.assertTrue(saved["body_matches_product"])

    def test_whitespace_reasoning_counts_length_but_is_not_nonempty(self):
        observer = diagnostic.Observer()
        for reasoning in ("", " \n\t", None):
            observer.observe({"choices": [{"message": {
                "content": "正文", "reasoning_content": reasoning},
                "finish_reason": "stop"}]}, False)
        self.assertEqual(observer.stats["reasoning_field_events"], 3)
        self.assertEqual(observer.stats["reasoning_characters"], 3)
        self.assertEqual(observer.stats["reasoning_nonempty_events"], 0)

    def test_reasoning_in_discarded_middle_event_stays_in_full_counts(self):
        observer = diagnostic.Observer()
        for index in range(300):
            message = {"content": "正文"}
            if index == 150:
                message["reasoning_content"] = "隐藏思考"
            observer.observe({"choices": [{"delta": message, "finish_reason": None}]}, True)
        self.assertEqual(observer.stats["events"], 300)
        self.assertEqual(observer.stats["reasoning_nonempty_events"], 1)
        self.assertEqual(observer.stats["reasoning_characters"], 4)
        self.assertEqual(len(observer.shapes), 128)
        self.assertNotIn(151, [shape["event"] for shape in observer.shapes])
        saved = observer.saved_stats({"text": "正文" * 300}, "")
        self.assertTrue(saved["events_truncated"])
        self.assertNotIn("隐藏思考", json.dumps(saved, ensure_ascii=False))

    def test_malformed_shapes_and_non_numeric_usage_are_not_recorded_as_text(self):
        observer = diagnostic.Observer()
        observer.observe({"choices": [{"message": {
            "content": ["unsupported"], "reasoning_content": ["unsupported"]},
            "finish_reason": "stop"}], "usage": {
                "prompt_tokens": "not-a-counter-private"}}, False)
        saved = observer.saved_stats({"text": ""}, "")
        self.assertGreaterEqual(saved["unexplained_shapes"], 3)
        self.assertEqual(saved["raw_numeric_usage"], [])
        self.assertNotIn("not-a-counter-private", json.dumps(saved))
        self.assertNotIn("unsupported", json.dumps(saved))

    def test_missing_credentials_does_not_reserve_request(self):
        self.prepare()
        with patch.object(diagnostic.os, "environ", {}), \
                patch.object(provider, "call_chat_completion",
                             side_effect=AssertionError("No real call allowed")):
            with self.assertRaises(ValueError):
                diagnostic.execute("merged_nonstream", self.out)
        self.assertFalse((self.out / "request-ledger.json").exists())

    def test_frozen_body_changed_stops_before_reading_key(self):
        self.prepare()
        (self.out / "frozen-request.body.json").write_bytes(b"changed")
        with patch.object(diagnostic.os, "environ", NoCredentialReads()):
            with self.assertRaises(ValueError):
                diagnostic.execute("merged_nonstream", self.out)

    def test_first_request_passes_mechanical_gates_but_needs_factual_review(self):
        self.prepare()
        result = self.run_first()
        self.assertTrue(all(result["checks"].values()), result["checks"])
        self.assertTrue(result["factual_review_required"])
        self.assertFalse(result["next_stage_eligible"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["cumulative_after"]["requests_reserved"], 35)
        self.assertEqual(result["cumulative_after"]["known_lower_bound"], 63876 + 46)
        self.assertFalse(self.calls[0]["stream"])
        self.assertNotIn("offline-fixture-private-token",
            (self.out / "samples/merged_nonstream.json").read_text())

    def test_reserved_first_request_cannot_be_replayed(self):
        self.prepare()
        self.run_first()
        with patch.object(diagnostic.os, "environ", NoCredentialReads()):
            with self.assertRaises(ValueError):
                diagnostic.execute("merged_nonstream", self.out)
        self.assertEqual(len(self.calls), 1)

    def test_nonempty_reasoning_blocks_and_does_not_enter_saved_text(self):
        self.prepare()
        result = self.run_first(reasoning="本次私密推理")
        self.assertFalse(result["checks"]["no_nonempty_reasoning"])
        self.assertFalse(result["factual_review_required"])
        path = self.out / "samples/merged_nonstream.json"
        self.assertNotIn("本次私密推理", path.read_text())
        review_path, _review = self.make_review()
        with self.assertRaises(ValueError):
            diagnostic.review_stage_one(review_path, self.out)
        with patch.object(diagnostic.os, "environ", NoCredentialReads()):
            with self.assertRaises((ValueError, FileNotFoundError)):
                diagnostic.execute("stability_nonstream", self.out)
        self.assertEqual(len(self.calls), 1)

    def test_alternate_reasoning_field_matches_product_summary_and_blocks(self):
        self.prepare()
        result = self.run_first(reasoning="别名字段中的私密文字", reasoning_field="reasoning")
        self.assertFalse(result["checks"]["no_nonempty_reasoning"])
        self.assertTrue(result["checks"]["reasoning_summary_matches"])
        self.assertTrue(result["checks"]["actual_stop_complete"])
        self.assertFalse(result["factual_review_required"])
        self.assertNotIn("别名字段中的私密文字",
                         (self.out / "samples/merged_nonstream.json").read_text())
        with patch.object(diagnostic.os, "environ", NoCredentialReads()):
            with self.assertRaises((ValueError, FileNotFoundError)):
                diagnostic.execute("stability_nonstream", self.out)
        self.assertEqual(len(self.calls), 1)

    def test_invalid_reasoning_shapes_match_product_counts_without_saving_values(self):
        for number, value in enumerate(({"text": "未知形状里的私密文字"}, [], False)):
            with self.subTest(value_type=type(value).__name__):
                separate = Path(self.temporary.name) / f"invalid-shape-{number}"
                separate.mkdir()
                previous = self.out
                self.out = separate
                try:
                    self.prepare()
                    result = self.run_first(reasoning=value)
                    self.assertFalse(result["checks"]["no_invalid_reasoning_shapes"])
                    self.assertFalse(result["checks"]["all_shapes_explained"])
                    self.assertTrue(result["checks"]["reasoning_summary_matches"])
                    self.assertTrue(result["checks"]["actual_stop_complete"])
                    self.assertFalse(result["factual_review_required"])
                    record_path = self.out / "samples/merged_nonstream.json"
                    record = evaluation.read_json(record_path)
                    self.assertEqual(record["result"]["reasoning_observation"]["invalid_events"], 1)
                    self.assertNotIn("未知形状里的私密文字", record_path.read_text())
                    with patch.object(diagnostic.os, "environ", NoCredentialReads()):
                        with self.assertRaises((ValueError, FileNotFoundError)):
                            diagnostic.execute("stability_nonstream", self.out)
                finally:
                    self.out = previous
        self.assertEqual(len(self.calls), 3)

    def test_short_or_missing_usage_first_stops_without_a_second_request(self):
        for name, options in (("short", {"text": "铁"}), ("no-usage", {"usage": False})):
            with self.subTest(name=name):
                separate = Path(self.temporary.name) / name
                separate.mkdir()
                previous = self.out
                self.out = separate
                try:
                    self.prepare()
                    result = self.run_first(**options)
                    self.assertFalse(result["factual_review_required"])
                    with patch.object(diagnostic.os, "environ", NoCredentialReads()):
                        with self.assertRaises((ValueError, FileNotFoundError)):
                            diagnostic.execute("stability_nonstream", self.out)
                finally:
                    self.out = previous
        self.assertEqual(len(self.calls), 2)

    def test_length_finish_is_preserved_and_stops_the_second_stage(self):
        self.prepare()
        result = self.run_first(finish="length")
        self.assertFalse(result["checks"]["actual_stop_complete"])
        record = evaluation.read_json(self.out / "samples/merged_nonstream.json")
        self.assertEqual(record["result"]["finish_reason"], "length")
        self.assertEqual(record["result"]["completion_state"], "truncated")
        self.assertEqual(record["observer"]["finish_reasons"], ["length"])
        with patch.object(diagnostic.os, "environ", NoCredentialReads()):
            with self.assertRaises((ValueError, FileNotFoundError)):
                diagnostic.execute("stability_nonstream", self.out)
        self.assertEqual(len(self.calls), 1)

    def test_second_needs_independent_valid_citations_before_credentials(self):
        self.prepare()
        self.run_first()
        with patch.object(diagnostic.os, "environ", NoCredentialReads()):
            with self.assertRaises((ValueError, FileNotFoundError)):
                diagnostic.execute("stability_nonstream", self.out)
        review_path, review = self.make_review()
        review["checks"][diagnostic.FACTUAL_CHECKS[0]]["evidence"][0]["quote"] = "别轮文字"
        evaluation.write_json(review_path, review)
        with self.assertRaises(ValueError):
            diagnostic.review_stage_one(review_path, self.out)
        self.assertFalse((self.out / "stage-one-review.json").exists())

    def test_failed_factual_review_is_preserved_and_blocks_second(self):
        self.prepare()
        self.run_first()
        review_path, _review = self.make_review(passed=False)
        reviewed = diagnostic.review_stage_one(review_path, self.out)
        self.assertFalse(reviewed["second_stage_permitted"])
        before = (self.out / "stage-one-review.json").read_bytes()
        review_path, _review = self.make_review(passed=True)
        with self.assertRaises(ValueError):
            diagnostic.review_stage_one(review_path, self.out)
        self.assertEqual((self.out / "stage-one-review.json").read_bytes(), before)
        with patch.object(diagnostic.os, "environ", NoCredentialReads()):
            with self.assertRaises(ValueError):
                diagnostic.execute("stability_nonstream", self.out)
        self.assertEqual(len(self.calls), 1)

    def test_approved_second_has_same_body_new_identity_and_cannot_repeat(self):
        self.prepare()
        self.run_first()
        review_path, _review = self.make_review()
        reviewed = diagnostic.review_stage_one(review_path, self.out)
        self.assertTrue(reviewed["second_stage_permitted"])
        with patch.object(diagnostic.os, "environ",
                          {"MOYU_AI_API_KEY": "offline-fixture-private-token"}), \
                patch.object(provider, "call_chat_completion", side_effect=self.responder), \
                patch.object(diagnostic.http.client.HTTPConnection, "getresponse",
                             return_value=Response()):
            second = diagnostic.execute("stability_nonstream", self.out)
        self.assertTrue(all(second["checks"].values()), second["checks"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["messages"], self.calls[1]["messages"])
        self.assertNotEqual(self.calls[0]["request_id"], self.calls[1]["request_id"])
        self.assertFalse(self.calls[1]["stream"])
        self.assertEqual(second["cumulative_after"]["requests_reserved"], 36)
        self.assertEqual(second["cumulative_after"]["known_lower_bound"], 63876 + 92)
        with patch.object(diagnostic.os, "environ", NoCredentialReads()):
            with self.assertRaises(ValueError):
                diagnostic.execute("stability_nonstream", self.out)

    def test_source_or_historical_ledger_change_blocks_before_credential_read(self):
        self.prepare()
        for helper in ("source_snapshot", "history_snapshot"):
            with self.subTest(helper=helper), \
                    patch.object(diagnostic, helper, return_value={"changed": True}), \
                    patch.object(diagnostic.os, "environ", NoCredentialReads()):
                with self.assertRaises(ValueError):
                    diagnostic.execute("merged_nonstream", self.out)
        self.assertFalse((self.out / "request-ledger.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
