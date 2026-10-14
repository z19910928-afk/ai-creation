#!/usr/bin/env python3
"""Typed analysis reuse respects current canonical artifacts and source evidence."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import analysis_candidate_reuse_http_fixtures as reuse
from guidance_alias_http_fixtures import Provider
from guidance_scope_change_http_fixtures import EvidenceResult
import server


PROJECT = reuse.PROJECT
KINDS = ("event", "character_state", "relationship", "foreshadowing", "fact")
SOURCES = (*reuse.SOURCES, "tests/analysis_candidate_typed_http_fixtures.py")


class AnalysisCandidateTypedHTTP(unittest.TestCase):
    observations = []
    process = reuse.AnalysisCandidateReuseHTTP.process
    preview = reuse.AnalysisCandidateReuseHTTP.preview
    body = reuse.AnalysisCandidateReuseHTTP.body
    state = reuse.AnalysisCandidateReuseHTTP.state
    change = reuse.AnalysisCandidateReuseHTTP.change
    call = reuse.AnalysisCandidateReuseHTTP.call
    observe = reuse.AnalysisCandidateReuseHTTP.observe
    database_hash = reuse.AnalysisCandidateReuseHTTP.database_hash
    canonical = reuse.AnalysisCandidateReuseHTTP.canonical
    manuscript = reuse.AnalysisCandidateReuseHTTP.manuscript
    complete = reuse.AnalysisCandidateReuseHTTP.complete
    confirm = reuse.AnalysisCandidateReuseHTTP.confirm
    assert_rejected_atomically = reuse.AnalysisCandidateReuseHTTP.assert_rejected_atomically

    def setUp(self):
        reuse.AnalysisCandidateReuseHTTP.setUp(self)
        status, self.other = self.call("POST", "/api/records", {
            "type": "人物", "name": "周闻", "description": "与林岚同行的合成人物。",
        })
        self.assertEqual(status, 201, self.other)
        self.fixture_body = (
            "林岚把左手指尖抵在铜制窗扣上，听见雨水落在石阶。"
            "同行的周闻站在北窗外，等她松开窗扣。"
            "她把铜钥匙藏进窗缝，留给周闻日后取用。"
        )
        status, result = self.call("PATCH", "/api/chapters/" + self.chapter_id, {
            "body": self.fixture_body,
        })
        self.assertEqual(status, 200, result)
        self.original_manuscript = self.manuscript()
        self.synthetic_mutations = []
        self.description_version = 0

    def tearDown(self):
        try:
            capture = os.environ.get("MOYU_ANALYSIS_CANDIDATE_TYPED_CAPTURE")
            if capture:
                target = Path(capture) / self._testMethodName
                target.mkdir(parents=True, exist_ok=False)
                with server.connect() as source, sqlite3.connect(target / "synthetic-book.sqlite3") as dest:
                    source.backup(dest)
                (target / "http-and-provider.json").write_text(json.dumps({
                    "synthetic_only": True, "paid_provider_calls": 0,
                    "http": self.exchanges, "provider_requests": Provider.requests,
                    "synthetic_database_mutations": self.synthetic_mutations,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            reuse.legacy.MaterialLegacyHTTP.tearDown(self)

    def raw(self, kind):
        quote = self.fixture_body
        evidence = {
            "source_chapter_id": self.chapter_id, "source_quote": quote,
            "source_start": 0, "source_end": len(quote),
        }
        candidate = {
            "kind": kind, "name": {
                "event": "窗前听雨", "character_state": "窗扣动作",
                "relationship": "同行关系", "foreshadowing": "窗缝铜钥匙",
                "fact": "窗扣材质",
            }[kind],
            "note": {
                "event": "林岚在北窗前听雨。", "character_state": "林岚的左手指尖抵着窗扣。",
                "relationship": "林岚与周闻同行。", "foreshadowing": "林岚把铜钥匙藏进窗缝。",
                "fact": "窗扣是铜制的。",
            }[kind],
            "subject_name": self.person["name"], "epistemic_mode": "world_truth",
            "known_by": [self.person["id"]], **evidence,
        }
        if kind == "event":
            candidate["story_time"] = "雨夜"
        elif kind == "character_state":
            candidate.update({
                "attribute": "当前动作", "value": {"动作": "抵着窗扣", "手": "左手"},
                "valid_start": "1", "valid_end": "2",
            })
        elif kind == "relationship":
            candidate.update({"target_name": self.other["name"], "predicate": "同行"})
        elif kind == "foreshadowing":
            candidate["foreshadowing_stage"] = "plant"
        elif kind == "fact":
            candidate.update({"valid_start": "1", "valid_end": "2"})
        return {
            "summary": {"text": "林岚在窗前听雨，周闻在窗外等候。", "evidence": [evidence]},
            "handoff": {"text": "铜钥匙仍藏在窗缝。", "evidence": [evidence]},
            "candidates": [candidate],
        }

    def accept(self, raw):
        self.description_version += 1
        self.change(description=f"合成首次分析资料版本 {self.description_version}。")
        first = self.complete(raw)
        status, response = self.confirm(first)
        self.assertEqual(status, 200, response)
        candidate = response["analysis"]["candidates"][0]
        self.assertEqual(candidate["status"], "accepted")
        self.assertTrue({"memory_id", "assertion_id", "source_span_id"} <=
                        set(reuse.artifact_ids(candidate)), candidate)
        return first, candidate

    def renew(self, raw, first):
        self.description_version += 1
        self.change(description=f"合成资料版本 {self.description_version}；正文和原候选保持相同。")
        second = self.complete(raw)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["candidates"][0]["id"], first["candidates"][0]["id"])
        return second

    def assert_reuse(self, kind):
        raw = self.raw(kind)
        first, accepted = self.accept(raw)
        second = self.renew(raw, first)
        before, calls = self.canonical(), len(Provider.requests)
        status, response = self.confirm(second)
        current = (response.get("analysis") or {}).get("candidates", [{}])[0]
        self.observe("unchanged_" + kind, kind=kind, confirm_http_status=status,
                     error=response.get("error"),
                     old_artifacts=reuse.artifact_ids(accepted),
                     new_artifacts=reuse.artifact_ids(current),
                     canonical_unchanged=self.canonical() == before,
                     manuscript_and_all_revisions_unchanged=self.manuscript() == self.original_manuscript,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(status, 200, response)
        self.assertEqual(reuse.artifact_ids(current), reuse.artifact_ids(accepted))
        self.assertEqual(self.canonical(), before)
        self.assertEqual(self.manuscript(), self.original_manuscript)
        self.assertEqual(len(Provider.requests), calls)

    def mutate_sql(self, scenario, sql, parameters):
        # These corruption controls touch only the temporary synthetic database.
        with server.connect() as conn:
            self.assertEqual(conn.execute(sql, parameters).rowcount, 1)
        self.synthetic_mutations.append({
            "scenario": scenario, "method": "synthetic_sql_only",
            "sql": sql, "parameters": list(parameters),
        })

    def mutate_artifact(self, kind, accepted, *, invalid):
        artifacts = accepted["artifacts"]
        if kind == "event":
            method, route = "PATCH", "/api/timeline/" + artifacts["timeline_id"]
            payload = {"status": "planned"} if invalid else {"summary": "作者已改时间线说明。"}
        elif kind == "character_state":
            method, route = ("DELETE" if invalid else "PATCH"), (
                "/api/character-states/" + artifacts["character_state_id"])
            payload = {} if invalid else {"value_json": {"动作": "已松开窗扣", "手": "左手"}}
        elif kind == "relationship":
            method, route = "PATCH", "/api/relationships/" + artifacts["relationship_id"]
            payload = {"status": "archived"} if invalid else {"note": "作者已改关系说明。"}
        elif kind == "foreshadowing":
            method = "DELETE" if invalid else "PATCH"
            route = ("/api/plot/" + artifacts["plot_id"] if invalid else
                     "/api/foreshadow-links/" + artifacts["foreshadow_link_id"])
            payload = {} if invalid else {"note": "作者已改伏笔说明。"}
        else:
            if not invalid:
                self.mutate_sql(
                    "changed_fact_assertion_object",
                    "UPDATE assertion SET object_json=? WHERE id=? AND project_id=?",
                    (server.json_text({"name": "窗扣材质", "type": "fact", "note": "作者已改正式事实说明。"}),
                     artifacts["assertion_id"], PROJECT),
                )
                return
            method = "POST"
            route = "/api/assertions/" + artifacts["assertion_id"] + "/review"
            payload = {"state": "retracted", "reason": "作者已撤回合成正式事实。"}
        status, response = self.call(method, route, payload)
        self.assertEqual(status, 200, response)

    def test_event_same_content_reuses_existing_artifacts(self):
        self.assert_reuse("event")

    def test_character_state_same_content_reuses_existing_artifacts(self):
        self.assert_reuse("character_state")

    def test_relationship_same_content_reuses_existing_artifacts(self):
        self.assert_reuse("relationship")

    def test_foreshadowing_same_content_reuses_existing_artifacts(self):
        self.assert_reuse("foreshadowing")

    def test_fact_same_content_reuses_existing_artifacts(self):
        self.assert_reuse("fact")

    def test_modified_typed_artifacts_cannot_be_blindly_reused(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                raw = self.raw(kind)
                first, accepted = self.accept(raw)
                self.mutate_artifact(kind, accepted, invalid=False)
                second = self.renew(raw, first)
                self.assert_rejected_atomically(second, "changed_typed_" + kind)

    def test_invalid_typed_artifacts_cannot_be_recreated(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                raw = self.raw(kind)
                first, accepted = self.accept(raw)
                self.mutate_artifact(kind, accepted, invalid=True)
                second = self.renew(raw, first)
                self.assert_rejected_atomically(second, "invalid_typed_" + kind)

    def test_withdrawn_memory_cannot_revive_any_candidate_type(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                raw = self.raw(kind)
                first, accepted = self.accept(raw)
                status, response = self.call(
                    "DELETE", "/api/memory/" + accepted["artifacts"]["memory_id"],
                    {"reason": "作者已撤回此类型的合成记忆。"},
                )
                self.assertEqual(status, 200, response)
                second = self.renew(raw, first)
                self.assert_rejected_atomically(second, "withdrawn_typed_memory_" + kind)

    def test_withdrawn_assertion_cannot_revive_any_candidate_type(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                raw = self.raw(kind)
                first, accepted = self.accept(raw)
                status, response = self.call(
                    "POST", "/api/assertions/" + accepted["artifacts"]["assertion_id"] + "/review",
                    {"state": "retracted", "reason": "作者已撤回此类型的合成正式事实。"},
                )
                self.assertEqual(status, 200, response)
                second = self.renew(raw, first)
                self.assert_rejected_atomically(second, "withdrawn_typed_assertion_" + kind)

    def test_author_changed_character_state_event_cannot_be_reused(self):
        raw = self.raw("character_state")
        first, accepted = self.accept(raw)
        status, event = self.call("POST", "/api/events", {
            "aggregate_id": self.person["id"],
            "event_type": "fixture.author_state_event",
            "payload": {"synthetic_only": True},
        })
        self.assertEqual(status, 201, event)
        status, state = self.call(
            "PATCH", "/api/character-states/" + accepted["artifacts"]["character_state_id"],
            {"event_id": event["id"]},
        )
        self.assertEqual(status, 200, state)
        self.assertEqual(state["event_id"], event["id"])
        second = self.renew(raw, first)
        self.assert_rejected_atomically(second, "author_changed_state_event_id")

    def test_relationship_target_new_alias_ambiguity_cannot_be_reused(self):
        raw = self.raw("relationship")
        first, _ = self.accept(raw)
        status, conflicting = self.call("POST", "/api/records", {
            "type": "人物", "name": "另一个周闻", "aliases": [self.other["name"]],
            "description": "与原关系目标存在相同别名的合成人物。",
        })
        self.assertEqual(status, 201, conflicting)
        self.assertNotEqual(conflicting["id"], self.other["id"])
        self.assertIn(self.other["name"], conflicting["aliases"])
        second = self.renew(raw, first)
        self.assert_rejected_atomically(second, "relationship_target_alias_ambiguity")

    def test_synthetically_changed_assertion_semantics_are_not_reused(self):
        for field, changed in (
            ("predicate", "synthetic_changed_predicate"),
            ("valid_start", "99"),
            ("valid_end", "100"),
        ):
            with self.subTest(field=field):
                raw = self.raw("fact")
                raw["candidates"][0]["name"] += "：" + field
                first, accepted = self.accept(raw)
                self.mutate_sql(
                    "assertion_corruption_" + field,
                    "UPDATE assertion SET " + field + "=? WHERE id=? AND project_id=?",
                    (changed, accepted["artifacts"]["assertion_id"], PROJECT),
                )
                second = self.renew(raw, first)
                self.assert_rejected_atomically(second, "synthetic_assertion_" + field)

    def test_synthetically_changed_source_span_is_not_reused(self):
        changed_quote = "合成损坏原句；此句不存在于当前正文。"
        fields = ("quote", "quote_hash", "start_codepoint", "end_codepoint",
                  "start_utf8", "end_utf8")
        for offset, field in enumerate(fields):
            with self.subTest(field=field):
                raw = self.raw("fact")
                candidate = raw["candidates"][0]
                candidate.update({
                    "name": candidate["name"] + "：" + field,
                    "source_start": offset, "source_quote": self.fixture_body[offset:],
                })
                first, accepted = self.accept(raw)
                changed = {
                    "quote": changed_quote, "quote_hash": server.hash_text(changed_quote),
                    "start_codepoint": offset + 1, "end_codepoint": len(self.fixture_body) - 1,
                    "start_utf8": len(self.fixture_body[:offset].encode("utf-8")) + 1,
                    "end_utf8": len(self.fixture_body.encode("utf-8")) - 1,
                }[field]
                self.mutate_sql(
                    "source_span_corruption_" + field,
                    "UPDATE source_span SET " + field + "=? WHERE id=? AND project_id=?",
                    (changed, accepted["artifacts"]["source_span_id"], PROJECT),
                )
                second = self.renew(raw, first)
                self.assert_rejected_atomically(second, "synthetic_source_span_" + field)


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def freeze_sources(phase, expected):
    capture = os.environ.get("MOYU_ANALYSIS_CANDIDATE_TYPED_CAPTURE")
    if not capture:
        return None
    target = Path(capture) / ("source-" + phase)
    target.mkdir(parents=True, exist_ok=False)
    for name in SOURCES:
        content = (ROOT / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected[name]:
            raise RuntimeError("Source changed before capture: " + name)
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (target / "fingerprints.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target)


def main():
    output = os.environ.get("MOYU_ANALYSIS_CANDIDATE_TYPED_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; choose a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    before_capture = freeze_sources("before", before)
    Provider.total_calls = 0
    AnalysisCandidateTypedHTTP.observations = []
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisCandidateTypedHTTP))
    after = fingerprints()
    after_capture = freeze_sources("after", after)
    report = reuse.safe_report({
        "passed": result.wasSuccessful() and before == after,
        "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(AnalysisCandidateTypedHTTP.observations),
        "synthetic_only": True, "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "frozen_source_before": before_capture, "frozen_source_after": after_capture,
        "source_unchanged": before == after,
        "scope": "temporary synthetic SQLite; actual saved AI jobs/reports; loopback HTTP; free local provider",
        "mutation_boundary": "author-editable typed artifacts changed through real HTTP; fact assertion object and source/assertion corruption controls use explicitly recorded synthetic SQL only",
        "claim_limit": "typed canonical artifact and source integrity only; no real manuscript, credential, literary-quality or first-tier claim",
    })
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
