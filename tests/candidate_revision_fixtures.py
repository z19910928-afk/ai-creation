#!/usr/bin/env python3
"""Revision concurrency, frozen source, retry and archive HTTP boundaries."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import moyu_backups  # noqa: E402
import server  # noqa: E402
from moyu_provider import ProviderError  # noqa: E402
from prose_output_http_fixtures import (  # noqa: E402
    BODY, UNICODE_PROSE, LocalProseProvider, ProseOutputHTTPTests,
)


class CandidateRevisionTests(ProseOutputHTTPTests):
    def _calls(self):
        with LocalProseProvider.lock:
            return copy.deepcopy(LocalProseProvider.calls)

    def _set_output(self, text):
        with LocalProseProvider.lock:
            LocalProseProvider.text = text

    def _records(self, job):
        with server.connect() as conn:
            saved_job = dict(conn.execute(
                "SELECT * FROM app_ai_job WHERE id=?", (job["id"],),
            ).fetchone())
            saved_run = dict(conn.execute(
                "SELECT * FROM ai_run WHERE id=?", (job["ai_run_id"],),
            ).fetchone())
        return saved_job, saved_run

    def _ledger(self):
        with server.connect() as conn:
            return tuple(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (self.project,),
            ).fetchone()[0] for table in (
                "app_ai_job", "ai_run", "app_quota_reservation", "app_candidate_revision",
            ))

    def _detail(self, job):
        status, detail = self.request("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, detail)
        return detail

    def _run_detail(self, job):
        status, detail = self.request("GET", "/api/ai/runs/" + job["ai_run_id"])
        self.assertEqual(status, 200, detail)
        return detail

    def _assert_public_job(self, job, *, notes=""):
        private_fields = {
            "context", "context_json", "instruction", "messages", "payload",
            "author_notes", "notes", "retry_consent", "run_status",
        }

        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(private_fields & value.keys(),
                                 "Public job contains a private request field")
                for item in value.values():
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)

        inspect(job)
        if notes:
            self.assertNotIn(notes, json.dumps(job, ensure_ascii=False))

    def _parent(self, text="潮" * 18, **options):
        parent = self.enqueue(text, **options)
        self.process(parent)
        return parent

    def _revision_payload(self, text, *, request_id="revision-boundary", notes=""):
        return {
            "request_id": request_id, "candidate_hash": server.hash_text(text),
            "notes": notes, "defer": True,
        }

    def _create_revision(self, parent, text, **options):
        payload = self._revision_payload(text, **options)
        status, result = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise", payload,
        )
        self.assertEqual(status, 202, result)
        return result["job"], payload

    def _assert_revision_blocked(self, parent, payload, *, status=400):
        ledger, calls = self._ledger(), self._calls()
        actual, rejected = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise", payload,
        )
        self.assertEqual(actual, status, rejected)
        self.assertEqual(self._ledger(), ledger)
        self.assertEqual(self._calls(), calls)
        return rejected

    def _fail_before_dispatch(self, job):
        failure = ProviderError(
            "fixture transport did not dispatch", code="fixture_not_dispatched",
            request_dispatched=False,
        )
        with patch("server.call_ai_provider", side_effect=failure):
            status, failed = self.request(
                "POST", f"/api/ai/jobs/{job['id']}/process", {},
            )
        self.assertEqual((status, failed["status"]), (200, "failed"), failed)
        self.assertEqual(failed["result"]["billing_state"], "not_dispatched")
        return failed

    def _deferred_retry(self, job):
        with patch("server.start_ai_worker", return_value=False):
            status, retried = self.request(
                "POST", f"/api/ai/jobs/{job['id']}/retry", {"mode": "original"},
            )
        self.assertEqual(status, 202, retried)
        return retried

    def test_parallel_revision_reserves_only_one_last_quota_slot(self):
        text = "潮" * 18
        parent = self._parent(text)
        status, settings = self.request("PUT", "/api/ai/settings", {"daily_limit": 2})
        self.assertEqual(status, 200, settings)
        parent_records = self._records(parent)
        gate = threading.Barrier(3)
        outcomes, errors = [], []
        lock = threading.Lock()

        def submit(identifier):
            try:
                gate.wait(timeout=5)
                result = self.request(
                    "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise",
                    self._revision_payload(text, request_id=identifier),
                )
                with lock:
                    outcomes.append(result)
            except Exception as error:
                with lock:
                    errors.append(error)

        threads = [threading.Thread(target=submit, args=(identifier,)) for identifier in
                   ("parallel-revision-a", "parallel-revision-b")]
        for thread in threads:
            thread.start()
        gate.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 2)
        self.assertEqual([status for status, _ in outcomes], [202, 202], outcomes)
        self.assertEqual(len({item["job"]["id"] for _, item in outcomes}), 1)
        self.assertEqual(sorted(item["idempotent"] for _, item in outcomes), [False, True])
        self.assertEqual(self._ledger(), (2, 2, 2, 1))
        self.assertEqual(len(self._calls()), 1)
        status, quota = self.request("GET", "/api/ai/quota")
        self.assertEqual(status, 200, quota)
        self.assertEqual((quota["used_runs"], quota["remaining_runs"]), (2, 0))
        self.assertEqual(self._records(parent), parent_records)

    def test_replayed_revision_returns_completed_child_after_manuscript_changes(self):
        text = "潮" * 18
        parent = self._parent(text)
        child, payload = self._create_revision(parent, text)
        self._set_output("雨" * 12)
        self.process(child)
        status, changed = self.request(
            "PATCH", "/api/chapters/" + self.chapter_id,
            {"body": BODY + "\n作者已经继续写作。"},
        )
        self.assertEqual(status, 200, changed)
        before, calls = self._ledger(), self._calls()
        status, replayed = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise", payload,
        )
        self.assertEqual(status, 202, replayed)
        self.assertTrue(replayed["idempotent"])
        self.assertEqual(replayed["job"]["id"], child["id"])
        self.assertEqual(replayed["job"]["status"], "success")
        self.assertEqual(replayed["job"]["result"]["text"], "雨" * 12)
        self.assertEqual(self._ledger(), before)
        self.assertEqual(self._calls(), calls)

    def test_same_request_identity_cannot_be_reused_for_another_parent(self):
        text = "潮" * 18
        first = self._parent(text)
        second = self._parent(text)
        self._create_revision(first, text, request_id="shared-revision-id")
        self._assert_revision_blocked(
            second, self._revision_payload(text, request_id="shared-revision-id"),
            status=409,
        )

    def test_revision_requires_an_explicit_valid_request_identity(self):
        for identifier in (None, 123, False, "", "invalid request id"):
            with self.subTest(identifier=identifier):
                parent = self._parent()
                payload = self._revision_payload("潮" * 18)
                if identifier is None:
                    payload.pop("request_id")
                else:
                    payload["request_id"] = identifier
                self._assert_revision_blocked(parent, payload)

    def test_revision_parent_cannot_cross_project_or_branch(self):
        text = "潮" * 18
        parent = self._parent(text)
        original_scope = self.project, self.branch
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO branch(id,project_id,status,acl_json) VALUES(?,?,?,?)",
                ("other-writing-branch", self.project, "active", "{}"),
            )
        self.branch = "other-writing-branch"
        try:
            self._assert_revision_blocked(
                parent, self._revision_payload(text, request_id="other-branch-request"),
            )
        finally:
            self.project, self.branch = original_scope
        status, created = self.request("POST", "/api/projects", {"title": "另一部作品"})
        self.assertEqual(status, 201, created)
        self.project, self.branch = created["project"]["id"], created["project"]["branch_id"]
        try:
            self._assert_revision_blocked(
                parent, self._revision_payload(text, request_id="other-project-request"),
            )
        finally:
            self.project, self.branch = original_scope

    def test_run_detail_reads_parent_and_child_without_dispatch_or_recent_queue(self):
        notes = "只用于整理请求的私有备注标记"
        parent = self._parent()
        child, _ = self._create_revision(parent, "潮" * 18, notes=notes)
        self._set_output(UNICODE_PROSE)
        self.process(child)
        stored_job, stored_run = self._records(child)
        stored_result = json.loads(stored_job["result_json"])
        stored_audit = json.loads(stored_run["consent_json"])["request_audit"]
        with server.connect() as conn:
            conn.execute("UPDATE app_ai_job SET created_at=? WHERE id=?",
                         ("2000-01-01T00:00:00+00:00", parent["id"]))
            conn.execute("UPDATE app_ai_job SET created_at=? WHERE id=?",
                         ("2001-01-01T00:00:00+00:00", child["id"]))
        before, calls = self._ledger(), self._calls()
        status, queue = self.request("GET", "/api/ai/queue?limit=1")
        self.assertEqual(status, 200, queue)
        self.assertNotIn(parent["id"], {item["id"] for item in queue["jobs"]})
        parent_detail = self._run_detail(parent)
        self.assertEqual(parent_detail["job"]["id"], parent["id"])
        self.assertEqual(parent_detail["job"]["result"]["text"], "潮" * 18)
        recovered = parent_detail["candidate_revision_child"]
        self.assertEqual(recovered["id"], child["id"])
        self.assertEqual(recovered["status"], "success")
        self.assertEqual(recovered["result"]["text"], UNICODE_PROSE)
        self.assertEqual(recovered["candidate_revision"]["parent_run_id"], parent["ai_run_id"])
        child_detail = self._run_detail(child)
        self.assertEqual(child_detail["job"]["id"], child["id"])
        self.assertEqual(child_detail["job"]["result"]["text"], UNICODE_PROSE)
        self.assertNotIn("candidate_revision_child", child_detail)
        self.assertEqual(child_detail["consent"]["request_audit"], stored_audit)
        self.assertEqual(recovered["result"]["request_audit"],
                         {key: value for key, value in stored_audit.items() if key != "payload"})
        self.assertEqual(recovered["result"]["usage"], stored_result["usage"])
        self.assertIn(notes, json.dumps(stored_audit["payload"], ensure_ascii=False))
        for public in (parent_detail["job"], recovered, child_detail["job"],
                       self._detail(child), *queue["jobs"]):
            self._assert_public_job(public, notes=notes)
        self.assertEqual(self._ledger(), before)
        self.assertEqual(self._calls(), calls)

    def test_run_detail_returns_latest_revision_retry_in_a_multistep_chain(self):
        notes = "历史重试链中的私有整理备注"
        parent = self._parent()
        child, _ = self._create_revision(parent, "潮" * 18, notes=notes)
        self._fail_before_dispatch(child)
        first = self._deferred_retry(child)
        self._fail_before_dispatch(first)
        latest = self._deferred_retry(first)
        self._set_output("雨" * 12)
        self.process(latest)
        before, calls = self._ledger(), self._calls()
        detail = self._run_detail(parent)
        recovered = detail["candidate_revision_child"]
        self.assertEqual(recovered["id"], latest["id"])
        self.assertEqual(recovered["retry_of"], first["ai_run_id"])
        self.assertEqual(recovered["status"], "success")
        self.assertEqual(recovered["result"]["text"], "雨" * 12)
        self.assertEqual(recovered["candidate_revision"]["parent_run_id"], parent["ai_run_id"])
        self.assertEqual(self._run_detail(child)["job"]["status"], "failed")
        self.assertEqual(self._run_detail(first)["job"]["status"], "failed")
        self.assertEqual(self._run_detail(latest)["job"]["id"], latest["id"])
        for job in (child, first, latest):
            self._assert_public_job(self._run_detail(job)["job"], notes=notes)
        self._assert_public_job(recovered, notes=notes)
        self.assertEqual(self._ledger(), before)
        self.assertEqual(self._calls(), calls)

    def test_run_detail_parent_and_child_are_not_visible_across_project_or_branch(self):
        parent = self._parent()
        child, _ = self._create_revision(parent, "潮" * 18)
        original_scope = self.project, self.branch
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO branch(id,project_id,status,acl_json) VALUES(?,?,?,?)",
                ("other-reading-branch", self.project, "active", "{}"),
            )
        calls = self._calls()
        self.branch = "other-reading-branch"
        try:
            for job in (parent, child):
                for kind, identifier in (("runs", job["ai_run_id"]), ("jobs", job["id"])):
                    status, rejected = self.request("GET", f"/api/ai/{kind}/{identifier}")
                    self.assertEqual(status, 404, rejected)
                    self.assertNotIn("job", rejected)
                    self.assertNotIn("candidate_revision_child", rejected)
        finally:
            self.project, self.branch = original_scope
        status, created = self.request("POST", "/api/projects", {"title": "另一部只读检查作品"})
        self.assertEqual(status, 201, created)
        self.project, self.branch = created["project"]["id"], created["project"]["branch_id"]
        try:
            for job in (parent, child):
                for kind, identifier in (("runs", job["ai_run_id"]), ("jobs", job["id"])):
                    status, rejected = self.request("GET", f"/api/ai/{kind}/{identifier}")
                    self.assertEqual(status, 404, rejected)
                    self.assertNotIn("job", rejected)
                    self.assertNotIn("candidate_revision_child", rejected)
        finally:
            self.project, self.branch = original_scope
        self.assertEqual(self._calls(), calls)

    def test_queued_revision_refuses_changed_manuscript_and_releases_reservation(self):
        parent = self._parent()
        child, _ = self._create_revision(parent, "潮" * 18)
        status, changed = self.request(
            "PATCH", "/api/chapters/" + self.chapter_id,
            {"body": BODY + "\n这句由作者在排队期间补写。"},
        )
        self.assertEqual(status, 200, changed)
        calls = self._calls()
        status, failed = self.request(
            "POST", f"/api/ai/jobs/{child['id']}/process", {},
        )
        self.assertEqual((status, failed["status"]), (200, "failed"), failed)
        self.assertEqual(failed["result"]["billing_state"], "not_dispatched")
        self.assertEqual(failed["result"]["error_code"], "dispatch_precondition_failed")
        self.assertEqual(self._calls(), calls)
        with server.connect() as conn:
            reservation = conn.execute(
                "SELECT status FROM app_quota_reservation WHERE ai_run_id=?",
                (child["ai_run_id"],),
            ).fetchone()[0]
            run_status = conn.execute(
                "SELECT status FROM ai_run WHERE id=?", (child["ai_run_id"],),
            ).fetchone()[0]
        self.assertEqual((reservation, run_status), ("released", "failed"))

    def test_queued_revision_refuses_parent_rejected_after_creation(self):
        parent = self._parent()
        child, _ = self._create_revision(parent, "潮" * 18)
        status, rejected = self.request(
            "POST", "/api/ai/decision",
            {"ai_run_id": parent["ai_run_id"], "decision": "rejected"},
        )
        self.assertEqual(status, 201, rejected)
        calls = self._calls()
        status, failed = self.request(
            "POST", f"/api/ai/jobs/{child['id']}/process", {},
        )
        self.assertEqual((status, failed["status"]), (200, "failed"), failed)
        self.assertEqual(failed["result"]["billing_state"], "not_dispatched")
        self.assertEqual(self._calls(), calls)

    def test_tampered_parent_transport_cannot_create_a_revision(self):
        parent = self._parent()
        with server.connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM app_ai_job WHERE id=?", (parent["id"],),
            ).fetchone()
            result = json.loads(row[0])
            result["text"] = "改" * 18
            conn.execute(
                "UPDATE app_ai_job SET result_json=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), parent["id"]),
            )
        rejected = self._assert_revision_blocked(
            parent, self._revision_payload("改" * 18),
        )
        self.assertIn("校验失败", rejected["error"])

    def test_tampered_json_body_cannot_disagree_with_original_transport(self):
        parent = self._parent(response_format="json")
        with server.connect() as conn:
            result = json.loads(conn.execute(
                "SELECT result_json FROM app_ai_job WHERE id=?", (parent["id"],),
            ).fetchone()[0])
            result["structured"]["content"] = "改" * 18
            conn.execute(
                "UPDATE app_ai_job SET result_json=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), parent["id"]),
            )
        self._assert_revision_blocked(parent, self._revision_payload("改" * 18))

    def test_json_polish_revision_keeps_selected_percentage_contract_and_original_prompt(self):
        text = "潮" * 18
        parent = self._parent(
            text, task="polish", response_format="json",
            instruction="润色正文，保持原文的80%至120%。",
        )
        original = self._records(parent)
        contract = self.frozen_contract(parent)
        self.assertEqual((contract["minimum_characters"], contract["maximum_characters"]), (8, 12))
        parent_payload = self._calls()[0]
        status, settings = self.request("PUT", "/api/ai/settings", {
            "model": "changed-model", "parameters": {"temperature": 0.9, "max_tokens": 9000},
            "styles": {"tone": "强烈", "instructions": "正文不少于500字"},
        })
        self.assertEqual(status, 200, settings)
        child, _ = self._create_revision(parent, text, notes="删掉重复动作，保留铜环去向")
        self._set_output(UNICODE_PROSE)
        completed = self.process(child)
        self.assertEqual(child["source"], parent["source"])
        self.assertEqual(self.frozen_contract(child), contract)
        self.assertEqual(completed["result"]["output_validation"]["contract"], contract)
        self.assertTrue(completed["result"]["output_validation"]["eligible"])
        sent = self._calls()[-1]
        self.assertEqual({key: value for key, value in sent.items() if key != "messages"},
                         {key: value for key, value in parent_payload.items() if key != "messages"})
        self.assertEqual(sent["messages"][1:-1], parent_payload["messages"][1:])
        self.assertTrue(sent["messages"][0]["content"].startswith(parent_payload["messages"][0]["content"]))
        self.assertNotIn("500字", sent["messages"][0]["content"])
        material = json.loads(sent["messages"][-1]["content"])
        self.assertEqual(material["candidate_text"], text)
        self.assertEqual(material["output_contract"], contract)
        self.assertEqual(self._records(parent), original)

    def test_failed_revision_concurrent_retries_create_one_successor_and_hide_saved_prompt(self):
        parent = self._parent()
        child, _ = self._create_revision(parent, "潮" * 18)
        self._fail_before_dispatch(child)
        gate = threading.Barrier(3)
        outcomes, errors = [], []
        lock = threading.Lock()

        def retry():
            try:
                gate.wait(timeout=5)
                response = self.request(
                    "POST", f"/api/ai/jobs/{child['id']}/retry", {"mode": "original"},
                )
                with lock:
                    outcomes.append(response)
            except Exception as error:
                with lock:
                    errors.append(error)

        with patch("server.start_ai_worker", return_value=False):
            threads = [threading.Thread(target=retry) for _ in range(2)]
            for thread in threads:
                thread.start()
            gate.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=15)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(status == 202 for status, _ in outcomes), outcomes)
        self.assertEqual(len({item["id"] for _, item in outcomes}), 1)
        self.assertEqual(sum(bool(item.get("idempotent")) for _, item in outcomes), 1)
        for _, item in outcomes:
            for private_field in ("retry_consent", "run_status", "context_json", "instruction"):
                self.assertNotIn(private_field, item)
        successor = outcomes[0][1]
        self.assertEqual(successor["retry_of"], child["ai_run_id"])
        self.assertEqual(self._ledger(), (3, 3, 3, 1))
        self.assertEqual(len(self._calls()), 1)
        self._set_output("雨" * 12)
        self.process(successor)
        before, calls = self._ledger(), self._calls()
        repeated = self._deferred_retry(child)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["id"], successor["id"])
        self.assertEqual(repeated["status"], "success")
        self.assertEqual(self._ledger(), before)
        self.assertEqual(self._calls(), calls)

    def test_unknown_retry_successor_blocks_both_retry_routes(self):
        parent = self._parent()
        child, _ = self._create_revision(parent, "潮" * 18)
        self._fail_before_dispatch(child)
        successor = self._deferred_retry(child)
        self._set_output("半段正文")
        with LocalProseProvider.lock:
            LocalProseProvider.incomplete = True
        status, uncertain = self.request(
            "POST", f"/api/ai/jobs/{successor['id']}/process", {},
        )
        self.assertEqual((status, uncertain["status"]), (200, "failed"), uncertain)
        self.assertEqual(uncertain["result"]["billing_state"], "unknown")
        self.assertEqual(uncertain["result"]["completion_state"], "unknown")
        before, calls = self._ledger(), self._calls()
        for kind, identifier in (("jobs", child["id"]), ("runs", child["ai_run_id"]),
                                 ("jobs", successor["id"]), ("runs", successor["ai_run_id"])):
            with self.subTest(kind=kind, identifier=identifier):
                status, rejected = self.request(
                    "POST", f"/api/ai/{kind}/{identifier}/retry", {"mode": "original"},
                )
                self.assertEqual(status, 409, rejected)
        self.assertEqual(self._ledger(), before)
        self.assertEqual(self._calls(), calls)

    def test_unknown_child_cannot_be_bypassed_with_another_revision_identity(self):
        text = "潮" * 18
        parent = self._parent(text)
        child, _ = self._create_revision(parent, text, request_id="first-revision")
        with LocalProseProvider.lock:
            LocalProseProvider.incomplete = True
        status, uncertain = self.request(
            "POST", f"/api/ai/jobs/{child['id']}/process", {},
        )
        self.assertEqual((status, uncertain["status"]), (200, "failed"), uncertain)
        before, calls = self._ledger(), self._calls()
        status, repeated = self.request(
            "POST", f"/api/ai/runs/{parent['ai_run_id']}/revise",
            self._revision_payload(text, request_id="different-revision"),
        )
        self.assertEqual(status, 202, repeated)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["job"]["id"], child["id"])
        self.assertEqual(repeated["job"]["status"], "failed")
        self.assertEqual(repeated["job"]["result"]["billing_state"], "unknown")
        self._assert_revision_blocked(
            parent, self._revision_payload(
                text, request_id="another-revision", notes="另加结尾",
            ), status=409,
        )
        self.assertEqual(self._ledger(), before)
        self.assertEqual(self._calls(), calls)

    def test_retry_of_revision_refuses_changed_source_before_new_reservation(self):
        parent = self._parent()
        child, _ = self._create_revision(parent, "潮" * 18)
        self._fail_before_dispatch(child)
        status, changed = self.request(
            "PATCH", "/api/chapters/" + self.chapter_id,
            {"body": BODY + "\n新正文不能套用旧整理任务。"},
        )
        self.assertEqual(status, 200, changed)
        before, calls = self._ledger(), self._calls()
        with patch("server.start_ai_worker", return_value=False):
            status, rejected = self.request(
                "POST", f"/api/ai/jobs/{child['id']}/retry", {"mode": "original"},
            )
        self.assertEqual(status, 409, rejected)
        self.assertEqual(self._ledger(), before)
        self.assertEqual(self._calls(), calls)

    def _assert_archive_read_only(self, project, branch, *, original_parent, original_child):
        original_scope = self.project, self.branch
        self.project, self.branch = project, branch
        try:
            status, queue = self.request("GET", "/api/ai/queue?limit=100")
            self.assertEqual(status, 200, queue)
            self.assertEqual(len(queue["jobs"]), 2)
            child = next(item for item in queue["jobs"] if item.get("candidate_revision"))
            parent = next(item for item in queue["jobs"] if not item.get("candidate_revision"))
            self.assertEqual(child["candidate_revision"]["parent_run_id"], parent["ai_run_id"])
            self.assertEqual(child["candidate_revision"]["root_run_id"], parent["ai_run_id"])
            self.assertNotEqual(parent["ai_run_id"], original_parent["ai_run_id"])
            self.assertNotEqual(child["ai_run_id"], original_child["ai_run_id"])
            self.assertTrue(all(item["restored_read_only"] for item in queue["jobs"]))
            before, calls = self._ledger(), self._calls()
            chapters = self.state()["chapters"]
            for restored in (parent, child):
                self._assert_revision_blocked(
                    restored, self._revision_payload(restored["result"]["text"]),
                )
                status, rejected = self.request(
                    "POST", f"/api/ai/jobs/{restored['id']}/retry", {"mode": "original"},
                )
                self.assertEqual(status, 409, rejected)
                status, rejected = self.request(
                    "POST", f"/api/ai/runs/{restored['ai_run_id']}/apply",
                    self.application(restored, restored["result"]["text"]),
                )
                self.assertEqual(status, 400, rejected)
                self.assertIn("仅供阅读", rejected["error"])
                status, existing = self.request(
                    "POST", f"/api/ai/jobs/{restored['id']}/process", {},
                )
                self.assertEqual(status, 200, existing)
                self.assertTrue(existing["restored_read_only"])
            status, drained = self.request("POST", "/api/ai/queue/drain", {})
            self.assertEqual(status, 200, drained)
            self.assertEqual(drained["processed"], 0)
            self.assertEqual(self.state()["chapters"], chapters)
            self.assertEqual(self._ledger(), before)
            self.assertEqual(self._calls(), calls)
        finally:
            self.project, self.branch = original_scope

    def test_backup_restore_and_duplicate_keep_lineage_readable_without_dispatch_or_apply(self):
        parent = self._parent("雨" * 12)
        child, _ = self._create_revision(parent, "雨" * 12)
        self._set_output("潮" * 12)
        self.process(child)
        original_parent, original_child = self._records(parent), self._records(child)
        original_chapters, calls = self.state()["chapters"], self._calls()
        status, archive = self.request("POST", "/api/backups", {})
        self.assertEqual(status, 201, archive)
        status, recovered = self.request(
            "POST", f"/api/backups/{archive['id']}/restore", {"confirm": True},
        )
        self.assertEqual(status, 201, recovered)
        self._assert_archive_read_only(
            recovered["project_id"], recovered["branch_id"],
            original_parent=parent, original_child=child,
        )
        status, duplicated = self.request(
            "POST", f"/api/projects/{self.project}/duplicate", {"title": "只读候选副本"},
        )
        self.assertEqual(status, 201, duplicated)
        copied = duplicated["project"]
        self._assert_archive_read_only(
            copied["id"], copied["branch_id"], original_parent=parent, original_child=child,
        )
        self.assertEqual(self._records(parent), original_parent)
        self.assertEqual(self._records(child), original_child)
        self.assertEqual(self.state()["chapters"], original_chapters)
        self.assertEqual(self._calls(), calls)

    def test_restore_preserves_candidate_equal_to_old_id_and_full_frozen_payload(self):
        text = self.chapter_id
        parent = self._parent(
            text, response_format="json", instruction="续写正文。",
        )
        child, _ = self._create_revision(parent, text)
        self._set_output(text)
        self.process(child)
        before = {job["ai_run_id"]: self._records(job) for job in (parent, child)}
        with server.connect() as conn:
            payload = moyu_backups.capture(conn, self.project)
            recovered = moyu_backups.transplant(conn, payload, "candidate-restore-book")
        with server.connect() as conn:
            for job in (parent, child):
                old_job, old_run = before[job["ai_run_id"]]
                new_job = dict(conn.execute(
                    "SELECT * FROM app_ai_job WHERE id=?",
                    (recovered["identity_map"][job["id"]],),
                ).fetchone())
                new_run = dict(conn.execute(
                    "SELECT * FROM ai_run WHERE id=?",
                    (recovered["identity_map"][job["ai_run_id"]],),
                ).fetchone())
                old_result, new_result = json.loads(old_job["result_json"]), json.loads(new_job["result_json"])
                self.assertEqual(new_result["text"].encode(), old_result["text"].encode())
                self.assertEqual(new_result["structured"]["content"].encode(), text.encode())
                self.assertEqual(new_run["output_hash"], old_run["output_hash"])
                self.assertEqual(json.loads(new_run["consent_json"])["request_audit"],
                                 json.loads(old_run["consent_json"])["request_audit"])
                old_context, new_context = json.loads(old_job["context_json"]), json.loads(new_job["context_json"])
                self.assertEqual(new_context["_source"]["range"]["quote"],
                                 old_context["_source"]["range"]["quote"])
                self.assertTrue(new_context["_restored_read_only"])
            self.assertEqual(moyu_backups.capture(conn, self.project), payload)
        self.assertEqual(len(self._calls()), 2)


def load_tests(_loader, _standard_tests, _pattern):
    # The imported base already has its own execution item in the release suite.
    return unittest.TestSuite(
        CandidateRevisionTests(name) for name in sorted(CandidateRevisionTests.__dict__)
        if name.startswith("test_")
    )


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(load_tests(None, None, None))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
