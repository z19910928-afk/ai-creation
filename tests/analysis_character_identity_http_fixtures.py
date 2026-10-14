#!/usr/bin/env python3
"""Character analysis candidates require current character identities."""
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


SOURCES = (*reuse.SOURCES, "tests/analysis_character_identity_http_fixtures.py")


class AnalysisCharacterIdentityHTTP(unittest.TestCase):
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

    def setUp(self):
        reuse.AnalysisCandidateReuseHTTP.setUp(self)
        status, self.other = self.call("POST", "/api/records", {
            "type": "人物", "name": "周闻", "description": "北窗外等待的合成人物。",
        })
        self.assertEqual(status, 201, self.other)
        self.fixture_body = (
            "林岚把左手指尖抵在铜制窗扣上，听见雨水落在石阶。"
            "周闻站在北窗外，等她松开窗扣。"
            "他们约好等雨停后一起离开。"
        )
        status, response = self.call("PATCH", "/api/chapters/" + self.chapter_id, {
            "body": self.fixture_body,
        })
        self.assertEqual(status, 200, response)
        self.original_manuscript = self.manuscript()

    def tearDown(self):
        try:
            capture = os.environ.get("MOYU_ANALYSIS_CHARACTER_IDENTITY_CAPTURE")
            if capture:
                target = Path(capture) / self._testMethodName
                target.mkdir(parents=True, exist_ok=False)
                with server.connect() as source, sqlite3.connect(target / "synthetic-book.sqlite3") as dest:
                    source.backup(dest)
                (target / "http-and-provider.json").write_text(json.dumps({
                    "synthetic_only": True, "paid_provider_calls": 0,
                    "http": self.exchanges, "provider_requests": Provider.requests,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            reuse.legacy.MaterialLegacyHTTP.tearDown(self)

    def raw(self, kind):
        evidence = {
            "source_chapter_id": self.chapter_id, "source_quote": self.fixture_body,
            "source_start": 0, "source_end": len(self.fixture_body),
        }
        candidate = {
            "kind": kind,
            "name": "窗扣动作" if kind == "character_state" else "共同离开",
            "note": "林岚的左手指尖抵着窗扣。" if kind == "character_state"
                    else "林岚与周闻约定雨停后一起离开。",
            "subject_name": self.person["name"],
            "epistemic_mode": "world_truth", "known_by": [self.person["id"]],
            **evidence,
        }
        if kind == "character_state":
            candidate.update({
                "attribute": "当前动作", "value": {"动作": "抵着窗扣", "手": "左手"},
                "valid_start": "1", "valid_end": "2",
            })
        else:
            candidate.update({"target_name": self.other["name"], "predicate": "同行"})
        return {
            "summary": {"text": "林岚在北窗听雨，周闻等她松开窗扣。", "evidence": [evidence]},
            "handoff": {"text": "两人准备在雨停后一起离开。", "evidence": [evidence]},
            "candidates": [candidate],
        }

    def edit_type(self, record, kind):
        status, response = self.call("PATCH", "/api/records/" + record["id"], {
            "type": kind,
        })
        self.assertEqual(status, 200, response)
        self.assertEqual(response["type"], kind)
        return response

    def subject_types(self, candidate):
        artifacts = candidate.get("artifacts") or {}
        with server.connect() as conn:
            if not artifacts.get("memory_id"):
                return None
            memory = conn.execute(
                "SELECT kind,entity_id FROM candidate_memory WHERE id=?",
                (artifacts["memory_id"],),
            ).fetchone()
            if memory is None:
                return None
            subject = conn.execute("SELECT kind FROM entity WHERE id=?",
                                   (memory["entity_id"],)).fetchone()
            result = {
                "memory_kind": memory["kind"],
                "subject_kind": subject["kind"] if subject else None,
            }
            if artifacts.get("relationship_id"):
                target = conn.execute(
                    "SELECT e.kind FROM app_story_relation r JOIN entity e ON e.id=r.to_entity_id "
                    "WHERE r.id=?", (artifacts["relationship_id"],),
                ).fetchone()
                result["target_kind"] = target["kind"] if target else None
            return result

    def assert_rejected(self, analysis, scenario, *, repeated):
        before, canonical, state = self.database_hash(), self.canonical(), self.state()
        calls = len(Provider.requests)
        status, response = self.confirm(analysis)
        candidate = (response.get("analysis") or {}).get("candidates", [{}])[0]
        observed = {
            "confirm_http_status": status, "error": response.get("error"),
            "new_analysis_current_at_confirmation": True,
            "database_unchanged": self.database_hash() == before,
            "canonical_unchanged": self.canonical() == canonical,
            "state_unchanged": self.state() == state,
            "manuscript_and_all_revisions_unchanged": self.manuscript() == self.original_manuscript,
            "extra_provider_calls": len(Provider.requests) - calls,
            "accepted_candidate_types": self.subject_types(candidate),
            "expected_rejection": [409] if repeated else [400, 409],
        }
        self.observe(scenario, **observed)
        self.assertIn(status, (409,) if repeated else (400, 409), response)
        self.assertEqual(self.database_hash(), before)
        self.assertEqual(self.canonical(), canonical)
        self.assertEqual(self.state(), state)
        self.assertEqual(self.manuscript(), self.original_manuscript)
        self.assertEqual(len(Provider.requests), calls)
        error = str(response.get("error") or "")
        self.assertRegex(error, r"[\u4e00-\u9fff]")
        self.assertRegex(error, r"人物|角色")
        self.assertNotIn("UNIQUE", error)

    def assert_fresh_rejected(self, kind, role):
        self.edit_type(self.person if role == "subject" else self.other, "设定")
        raw = self.raw(kind)
        analysis = self.complete(raw)
        status, listed = self.call("GET", "/api/chapter-analysis?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, listed)
        current = next(item for item in listed["analyses"] if item["id"] == analysis["id"])
        self.assertEqual(current["status"], "pending")
        self.assertFalse(current.get("stale"))
        self.assert_rejected(analysis, "fresh_" + kind + "_" + role + "_noncharacter",
                             repeated=False)

    def accept(self, raw):
        first = self.complete(raw)
        status, response = self.confirm(first)
        self.assertEqual(status, 200, response)
        candidate = response["analysis"]["candidates"][0]
        self.assertEqual(candidate["status"], "accepted")
        required = {"memory_id", "assertion_id", "source_span_id"}
        required.add("character_state_id" if candidate["kind"] == "character_state"
                     else "relationship_id")
        self.assertTrue(required <= set(reuse.artifact_ids(candidate)), candidate)
        types = self.subject_types(candidate)
        self.assertEqual(types["subject_kind"], "人物")
        if candidate["kind"] == "relationship":
            self.assertEqual(types["target_kind"], "人物")
        return first, candidate

    def assert_renewed_rejected(self, kind, role):
        raw = self.raw(kind)
        first, accepted = self.accept(raw)
        self.edit_type(self.person if role == "subject" else self.other, "设定")
        second = self.complete(raw)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["candidates"][0]["id"], first["candidates"][0]["id"])
        status, listed = self.call("GET", "/api/chapter-analysis?chapter_id=" + self.chapter_id)
        self.assertEqual(status, 200, listed)
        current = next(item for item in listed["analyses"] if item["id"] == second["id"])
        self.assertEqual(current["status"], "pending")
        self.assertFalse(current.get("stale"))
        self.observe("renewed_identity_changed", candidate_kind=kind, changed_role=role,
                     distinct_analysis_id=True, same_candidate_id=True,
                     stored_candidate_types=self.subject_types(accepted))
        self.assert_rejected(second, "renewed_" + kind + "_" + role + "_noncharacter",
                             repeated=True)

    def assert_normal(self, kind):
        raw = self.raw(kind)
        first, accepted = self.accept(raw)
        self.change(description="合成人物说明已更新，人物身份和原候选事实保持相同。")
        second = self.complete(raw)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["candidates"][0]["id"], first["candidates"][0]["id"])
        before, calls = self.canonical(), len(Provider.requests)
        status, response = self.confirm(second)
        candidate = (response.get("analysis") or {}).get("candidates", [{}])[0]
        self.observe("normal_" + kind + "_control",
                     first_accept_http_status=200, repeated_accept_http_status=status,
                     canonical_unchanged=self.canonical() == before,
                     artifact_ids_reused=reuse.artifact_ids(candidate) == reuse.artifact_ids(accepted),
                     candidate_types=self.subject_types(candidate),
                     manuscript_and_all_revisions_unchanged=self.manuscript() == self.original_manuscript,
                     extra_provider_calls=len(Provider.requests) - calls)
        self.assertEqual(status, 200, response)
        self.assertEqual(candidate["status"], "accepted")
        self.assertEqual(reuse.artifact_ids(candidate), reuse.artifact_ids(accepted))
        self.assertEqual(self.canonical(), before)
        self.assertEqual(self.manuscript(), self.original_manuscript)
        self.assertEqual(len(Provider.requests), calls)

    def test_fresh_character_state_rejects_noncharacter_subject(self):
        self.assert_fresh_rejected("character_state", "subject")

    def test_fresh_relationship_rejects_noncharacter_subject(self):
        self.assert_fresh_rejected("relationship", "subject")

    def test_fresh_relationship_rejects_noncharacter_target(self):
        self.assert_fresh_rejected("relationship", "target")

    def test_normal_character_state_accepts_and_reuses_current_character(self):
        self.assert_normal("character_state")

    def test_normal_relationship_accepts_and_reuses_current_characters(self):
        self.assert_normal("relationship")

    def test_renewed_character_state_rejects_subject_type_change(self):
        self.assert_renewed_rejected("character_state", "subject")

    def test_renewed_relationship_rejects_subject_type_change(self):
        self.assert_renewed_rejected("relationship", "subject")

    def test_renewed_relationship_rejects_target_type_change(self):
        self.assert_renewed_rejected("relationship", "target")


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def freeze_sources(phase, expected):
    capture = os.environ.get("MOYU_ANALYSIS_CHARACTER_IDENTITY_CAPTURE")
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
    output = os.environ.get("MOYU_ANALYSIS_CHARACTER_IDENTITY_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; choose a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    before_capture = freeze_sources("before", before)
    Provider.total_calls = 0
    AnalysisCharacterIdentityHTTP.observations = []
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisCharacterIdentityHTTP))
    after = fingerprints()
    after_capture = freeze_sources("after", after)
    report = reuse.safe_report({
        "passed": result.wasSuccessful() and before == after,
        "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(AnalysisCharacterIdentityHTTP.observations),
        "synthetic_only": True, "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "frozen_source_before": before_capture, "frozen_source_after": after_capture,
        "source_unchanged": before == after,
        "scope": "temporary synthetic SQLite; saved AI jobs; actual loopback HTTP; character identity type validation",
        "claim_limit": "character state and relationship identity integrity only; no literary-quality or first-tier claim",
    })
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
