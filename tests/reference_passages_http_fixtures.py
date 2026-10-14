#!/usr/bin/env python3
"""Explicit reference passages stay scoped, budgeted and source-bound."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from guidance_alias_http_fixtures import GuidanceAliasHTTP, Provider, encode
from guidance_scope_change_http_fixtures import EvidenceResult
import moyu_context
import moyu_import
import server


PROJECT = "default-project"
BRANCH = "main"
CANARY = "REFERENCE_PASSAGE_SYNTHETIC_CANARY"
QUOTE = CANARY + "。渡口的铃声停下，远舟的客人把蓝布收进箱底。🙂𠀀"
BODY = "未选择的前言。\n\n" + QUOTE + "\n\n未选择的章节尾声。"
PROFILE = {
    "max_tokens": 24_000, "chapter_top_k": 0, "recent_window": 0,
    "memory_top_k": 0, "record_top_k": 0,
}
SOURCES = (
    "server.py", "moyu_context.py", "moyu_import.py", "moyu_materials.py",
    "moyu_provider.py", "moyu_revision.py", "moyu_workspace.py",
    "moyu_backups.py", "scripts/sqlite_backup.py",
    "docs/schema-v1.sql", "docs/schema-v1.1-migration.sql",
    "tests/guidance_alias_http_fixtures.py",
    "tests/guidance_scope_change_http_fixtures.py",
    "tests/reference_passages_http_fixtures.py",
)


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def fingerprints():
    return {name: digest((ROOT / name).read_bytes()) for name in SOURCES}


def jsonable(value):
    if isinstance(value, bytes):
        return {"blob_bytes": len(value), "blob_sha256": digest(value)}
    if isinstance(value, dict):
        return {key: jsonable(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(child) for child in value]
    return value


def prompt_material(payload):
    user = next(item["content"] for item in payload["messages"] if item["role"] == "user")
    left, right = "<manuscript>\n", "\n</manuscript>"
    if not user.startswith(left) or right not in user:
        raise AssertionError("Expected the existing manuscript data wrapper")
    return json.loads(user[len(left):user.index(right)])


def stream_neutral(payload):
    value = copy.deepcopy(payload)
    value.pop("stream", None)
    value.pop("stream_options", None)
    return value


def stable_audit(audit):
    value = copy.deepcopy(audit)
    value.pop("request_id", None)
    value.pop("created_at", None)
    return value


class LocalReferenceProvider(Provider):
    raw_requests = []
    lock = threading.Lock()
    text = "雨水顺着窗框淌下，她松开抵着铜扣的手。"

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.loads(raw)
        with self.lock:
            Provider.requests.append(payload)
            Provider.total_calls += 1
            self.raw_requests.append(raw)
        response = encode({
            "model": "guidance-alias-fixture",
            "choices": [{
                "message": {"content": self.text}, "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110},
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class ReferencePassagesHTTP(unittest.TestCase):
    observations = []
    queue = GuidanceAliasHTTP.queue
    process = GuidanceAliasHTTP.process
    body = GuidanceAliasHTTP.body

    def setUp(self):
        self.exchanges = []
        self.original_db_path = server.DB_PATH
        GuidanceAliasHTTP.setUp(self)
        self.provider.RequestHandlerClass = LocalReferenceProvider
        LocalReferenceProvider.raw_requests = []
        self.request["retrieval_profile"] = dict(PROFILE)
        self.reference = self.seed_reference("selected")
        self.unconsumed = self.seed_reference("unconsumed", body="独立参考段落，不在请求中使用。")
        self.selection = self.select(self.reference)
        self.request["reference_selections"] = [copy.deepcopy(self.selection)]

    def tearDown(self):
        try:
            capture = os.environ.get("MOYU_REFERENCE_PASSAGES_CAPTURE")
            if capture:
                target = Path(capture) / self._testMethodName
                target.mkdir(parents=True, exist_ok=False)
                with server.connect() as source, sqlite3.connect(target / "synthetic-book.sqlite3") as destination:
                    source.backup(destination)
                (target / "http-and-provider.json").write_text(
                    json.dumps({
                        "synthetic_only": True, "paid_provider_calls": 0,
                        "http": self.exchanges, "provider_requests": Provider.requests,
                        "provider_request_bytes_sha256": [
                            digest(raw) for raw in LocalReferenceProvider.raw_requests
                        ],
                    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                )
        finally:
            GuidanceAliasHTTP.tearDown(self)
            server.DB_PATH = self.original_db_path

    def call(self, method, route, payload=None, project=PROJECT, branch=BRANCH):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            self.base + route, method=method,
            data=encode(payload) if payload is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Moyu-Project": project, "X-Moyu-Branch": branch,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status, result = response.status, json.load(response)
        except urllib.error.HTTPError as error:
            status, result = error.code, json.load(error)
        self.exchanges.append({
            "method": method, "route": route, "project": project, "branch": branch,
            "request": copy.deepcopy(payload), "status": status, "response": copy.deepcopy(result),
        })
        return status, result

    def preview(self, request=None):
        status, preview = self.call(
            "POST", "/api/ai/prompt-preview", self.request if request is None else request,
        )
        self.assertEqual(status, 200, preview)
        return preview

    def context_preview(self, request=None):
        request = self.request if request is None else request
        fields = {
            "chapter_id": request["chapter_id"], "task": request["task"],
            "instruction": request.get("instruction", ""),
            "retrieval_profile": json.dumps(request["retrieval_profile"], separators=(",", ":")),
        }
        if "reference_selections" in request:
            fields["reference_selections"] = json.dumps(
                request["reference_selections"], separators=(",", ":"),
            )
        if "source" in request:
            fields["source"] = json.dumps(request["source"], separators=(",", ":"))
        return self.call("GET", "/api/context?" + urlencode(fields))

    def observe(self, scenario, **details):
        self.observations.append({
            "test": self._testMethodName, "scenario": scenario, **details,
        })

    def seed_reference(self, label, *, project=PROJECT, branch=BRANCH, body=BODY):
        document_id, section_id = "reference_" + label, "reference_section_" + label
        original = ("合成参考原文件：" + label + "\n" + body).encode("utf-8")
        source_sha256 = digest(original)
        source = {
            "source_start": 7, "source_end": 7 + len(body),
            "source_hash": digest(body), "sources": [],
        }
        with server.connect() as conn:
            moyu_import.ensure_reference_tables(conn)
            conn.execute(
                "INSERT INTO app_reference_document VALUES(?,?,?,?,?,?,?,?,?,?)",
                (document_id, project, branch, label + ".txt", "合成参考书 " + label,
                 source_sha256, original, digest(body),
                 server.json_text({"synthetic_only": True, "characters": len(body)}), server.now()),
            )
            conn.execute(
                "INSERT INTO app_reference_section VALUES(?,?,?,?,?,?,?,?)",
                (section_id, document_id, project, branch, 0, "合成参考章 " + label,
                 body, server.json_text(source)),
            )
        return {
            "document_id": document_id, "section_id": section_id, "body": body,
            "source_sha256": source_sha256, "project_id": project, "branch_id": branch,
        }

    def select(self, reference, *, start=None, end=None):
        body = reference["body"]
        if start is None:
            start = body.index(QUOTE) if QUOTE in body else 0
        if end is None:
            end = start + len(QUOTE) if QUOTE in body else len(body)
        return {
            "document_id": reference["document_id"], "section_id": reference["section_id"],
            "section_start": start, "section_end": end,
            "source_sha256": reference["source_sha256"],
            "section_hash": digest(body), "source_hash": digest(body[start:end]),
        }

    def frozen(self, job):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT j.context_json,r.consent_json FROM app_ai_job j "
                "JOIN ai_run r ON r.id=j.ai_run_id WHERE j.id=?", (job["id"],),
            ).fetchone()
        return json.loads(row["context_json"]), json.loads(row["consent_json"])

    def snapshot(self):
        with server.connect() as conn:
            tables = [
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
                ) if not row[0].startswith("sqlite_")
            ]
            result = {}
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                rows = [jsonable(dict(row)) for row in conn.execute("SELECT * FROM " + quoted)]
                rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
                result[table] = {"count": len(rows), "rows_sha256": digest(encode(rows))}
            return result

    def canon_snapshot(self):
        all_tables = self.snapshot()
        names = (
            "document_node", "document_revision", "app_chapter_meta", "entity",
            "assertion", "source_span", "character_state", "candidate_memory", "plot_thread",
            "app_story_relation", "app_timeline_event", "app_foreshadow_link",
            "app_candidate_application", "app_candidate_revision",
        )
        return {name: all_tables[name] for name in names if name in all_tables}

    def change_reference(self, reference=None, *, field="body", remove=False):
        reference = reference or self.reference
        with server.connect() as conn:
            if remove:
                conn.execute("DELETE FROM app_reference_section WHERE id=?", (reference["section_id"],))
            elif field == "body":
                conn.execute(
                    "UPDATE app_reference_section SET body=body||? WHERE id=?",
                    ("\n合成修改，不改变任何真实作品。", reference["section_id"]),
                )
            elif field == "source_json":
                conn.execute(
                    "UPDATE app_reference_section SET source_json=? WHERE id=?",
                    (server.json_text({
                        "source_start": 9, "source_end": 9 + len(reference["body"]),
                        "source_hash": digest(reference["body"]), "sources": [],
                    }), reference["section_id"]),
                )
            elif field == "title":
                conn.execute(
                    "UPDATE app_reference_document SET title=? WHERE id=?",
                    ("修改后的合成来源标题", reference["document_id"]),
                )
            else:
                raise AssertionError("Unknown synthetic mutation")

    def passages(self, context, *, expected_quote=QUOTE):
        passages = context.get("reference_passages")
        self.assertIsInstance(passages, list)
        self.assertEqual(len(passages), 1, passages)
        passage, selected = passages[0], self.request["reference_selections"][0]
        self.assertEqual(passage.get("source_quote"), expected_quote, passage)
        for field in ("section_start", "section_end", "source_sha256", "section_hash"):
            self.assertEqual(passage.get(field), selected[field], passage)
        self.assertEqual(passage.get("source_hash"), digest(expected_quote), passage)
        self.assertEqual(passage.get("offset_unit"), "unicode_codepoint", passage)
        self.assertEqual(passage.get("source_document_id", passage.get("document_id")), selected["document_id"])
        self.assertEqual(passage.get("source_section_id", passage.get("section_id")), selected["section_id"])
        self.assertEqual(passage.get("material_kind"), "reference", passage)
        self.assertNotIn(passage.get("use"), (None, "story_fact", "world_truth"), passage)
        return passage

    def assert_bound_passage(self, job):
        context, consent = self.frozen(job)
        passage = self.passages(context)
        marker = context.get("_material_source")
        self.assertIsInstance(marker, dict)
        dependencies = [
            item for item in marker.get("dependencies", [])
            if item.get("kind") == "reference_section"
        ]
        self.assertEqual(len(dependencies), 1, marker)
        self.assertEqual(dependencies[0]["id"], self.selection["section_id"])
        self.assertEqual(len(dependencies[0]["sha256"]), 64)
        self.assertEqual(
            prompt_material(consent["request_audit"]["payload"])["reference_passages"],
            context["reference_passages"],
        )
        return context, consent, passage

    def assert_rejected_without_writes(self, request, scenario, *, statuses=(400,)):
        before, body, calls = self.snapshot(), self.body(), len(Provider.requests)
        outcomes = []
        for route in ("/api/ai/prompt-preview", "/api/ai/queue", "/api/ai", "/api/context"):
            if route == "/api/context":
                status, result = self.context_preview(request)
            else:
                status, result = self.call("POST", route, {**request, "defer": True})
            outcomes.append({"route": route, "status": status, "error": result.get("error")})
            self.assertIn(status, statuses, {
                "route": route, "status": status, "error": result.get("error"),
                "response_keys": sorted(result),
            })
            self.assertTrue(result.get("error"), route)
            self.assertEqual(self.snapshot(), before, (route, "database changed"))
            self.assertEqual(self.body(), body)
            self.assertEqual(len(Provider.requests), calls)
        self.observe(
            scenario, outcomes=outcomes, database_unchanged=self.snapshot() == before,
            body_unchanged=self.body() == body, extra_provider_calls=len(Provider.requests) - calls,
        )

    def assert_dispatch_blocked(self, job, scenario):
        context, consent = self.frozen(job)
        before, body, calls = self.canon_snapshot(), self.body(), len(Provider.requests)
        completed = self.process(job)
        after_context, after_consent = self.frozen(job)
        self.assertEqual(completed["status"], "failed", completed)
        self.assertEqual(completed["result"]["billing_state"], "not_dispatched", completed)
        self.assertEqual(len(Provider.requests), calls)
        self.assertEqual(self.body(), body)
        self.assertEqual(self.canon_snapshot(), before)
        self.assertEqual(after_context, context)
        self.assertEqual(after_consent["request_audit"], consent["request_audit"])
        self.observe(
            scenario, status=completed["status"], billing_state=completed["result"]["billing_state"],
            extra_provider_calls=len(Provider.requests) - calls,
            canon_unchanged=self.canon_snapshot() == before, body_unchanged=self.body() == body,
            frozen_context_unchanged=after_context == context,
        )

    def assert_retries_blocked(self, job, scenario):
        before, body, calls = self.snapshot(), self.body(), len(Provider.requests)
        outcomes = []
        with patch("server.start_ai_worker", return_value=False):
            for entry, identifier in (("jobs", job["id"]), ("runs", job["ai_run_id"])):
                status, result = self.call(
                    "POST", f"/api/ai/{entry}/{identifier}/retry", {"mode": "original"},
                )
                outcomes.append({"entry": entry, "status": status, "error": result.get("error")})
                self.assertEqual(status, 409, result)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.body(), body)
                self.assertEqual(len(Provider.requests), calls)
        self.observe(
            scenario, outcomes=outcomes, database_unchanged=self.snapshot() == before,
            body_unchanged=self.body() == body, extra_provider_calls=len(Provider.requests) - calls,
        )

    def apply(self, job, completed):
        return self.call("POST", "/api/ai/runs/" + job["ai_run_id"] + "/apply", {
            **job["source"], "decision": "accepted",
            "replacement": "\n\n" + completed["result"]["text"],
        })

    def revise(self, job, completed, *, request_id="synthetic-reference-revision"):
        return self.call("POST", "/api/ai/runs/" + job["ai_run_id"] + "/revise", {
            "request_id": request_id, "candidate_hash": digest(completed["result"]["text"]),
            "notes": "保持原参考来源，整理候选。", "defer": True,
        })

    def test_empty_selection_preserves_existing_prompt_and_payload(self):
        original = {key: value for key, value in self.request.items() if key != "reference_selections"}
        before = self.snapshot()
        absent, empty = self.preview(original), self.preview({**original, "reference_selections": []})
        self.assertEqual(absent["request"], empty["request"])
        self.assertEqual(absent["messages"], empty["messages"])
        self.assertEqual(stable_audit(absent["request_audit"]), stable_audit(empty["request_audit"]))
        for preview in (absent, empty):
            self.assertNotIn("reference_passages", preview)
            self.assertNotIn("reference_passages", prompt_material(preview["request"]))
        for request in (original, {**original, "reference_selections": []}):
            status, context = self.context_preview(request)
            self.assertEqual(status, 200, context)
            self.assertNotIn("reference_passages", context)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(Provider.requests, [])
        self.observe("no_selection_compatibility", exact_payload_equal=True, database_unchanged=True)

    def test_continue_preview_queue_and_provider_use_same_reference(self):
        before, body = self.canon_snapshot(), self.body()
        preview = self.preview()
        self.passages(prompt_material(preview["request"]))
        self.assertEqual(preview["reference_passages"], prompt_material(preview["request"])["reference_passages"])
        status, context_preview = self.context_preview()
        self.assertEqual(status, 200, context_preview)
        self.assertEqual(context_preview["reference_passages"], preview["reference_passages"])
        job = self.queue()
        context, consent, _ = self.assert_bound_passage(job)
        self.assertEqual(stream_neutral(preview["request"]), stream_neutral(consent["request_audit"]["payload"]))
        public_context = {key: value for key, value in context.items() if not key.startswith("_")}
        budget = context["context_budget"]
        self.assertEqual(budget["estimated_utf8_bytes"], len(moyu_context._json(public_context).encode("utf-8")))
        self.assertLessEqual(budget["estimated_utf8_bytes"], budget["max_context_utf8_bytes"])
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(len(Provider.requests), 1)
        self.assertEqual(Provider.requests[0], consent["request_audit"]["payload"])
        self.assertEqual(digest(LocalReferenceProvider.raw_requests[0]), consent["request_audit"]["request_sha256"])
        self.assertEqual(self.canon_snapshot(), before)
        self.assertEqual(self.body(), body)
        self.observe(
            "continue_exact_request", preview_context_queue_equal=True,
            frozen_provider_payload_equal=True, exact_request_bytes_equal=True,
            reference_codepoints=len(QUOTE), canon_unchanged=True, body_unchanged=True,
        )

    def test_polish_preview_queue_use_same_full_manuscript_selection_wrapper(self):
        body, start, end = self.body(), 4, 22
        self.request.update(task="polish", source={"range": {
            "start": start, "end": end, "quote": body[start:end],
        }})
        selections = []
        real_assemble = moyu_context.assemble_context

        def recording_assemble(*args, **kwargs):
            selections.append(copy.deepcopy(kwargs.get("selection")))
            return real_assemble(*args, **kwargs)

        with patch("moyu_context.assemble_context", side_effect=recording_assemble):
            preview, job = self.preview(), self.queue()
            context_status, context_preview = self.context_preview()
        context, consent, _ = self.assert_bound_passage(job)
        self.assertEqual(context_status, 200, context_preview)
        self.assertEqual(len(selections), 3)
        self.assertEqual(selections[0], selections[1])
        self.assertEqual(selections[0], selections[2])
        self.assertEqual(set(selections[0]), {"range", "revision_id", "chapter_hash"})
        self.assertEqual(selections[0]["range"], job["source"]["range"])
        self.assertEqual(selections[0]["revision_id"], job["source"]["source_revision_id"])
        self.assertEqual(selections[0]["chapter_hash"], job["source"]["source_body_hash"])
        self.assertEqual(context["current_chapter"]["body"], body[start:end])
        self.assertEqual(stream_neutral(preview["request"]), stream_neutral(consent["request_audit"]["payload"]))
        self.assertEqual(preview["reference_passages"], context["reference_passages"])
        self.assertEqual(context_preview["reference_passages"], context["reference_passages"])
        self.assertEqual(context_preview["current_chapter"]["body"], body[start:end])
        self.assertEqual(Provider.requests, [])
        self.observe("polish_wrapper_parity", exact_full_wrapper_equal=True, preview_queue_payload_equal=True)

    def test_polish_without_reference_also_has_full_wrapper_parity(self):
        body = self.body()
        self.request.pop("reference_selections")
        self.request.update(task="polish", source={"range": {
            "start": 2, "end": 21, "quote": body[2:21],
        }})
        selections = []
        real_assemble = moyu_context.assemble_context

        def recording_assemble(*args, **kwargs):
            selections.append(copy.deepcopy(kwargs.get("selection")))
            return real_assemble(*args, **kwargs)

        with patch("moyu_context.assemble_context", side_effect=recording_assemble):
            preview, job = self.preview(), self.queue()
        _, consent = self.frozen(job)
        self.assertEqual(selections[0], selections[1])
        self.assertEqual(set(selections[0]), {"range", "revision_id", "chapter_hash"})
        self.assertEqual(stream_neutral(preview["request"]), stream_neutral(consent["request_audit"]["payload"]))
        self.assertNotIn("reference_passages", preview)
        self.observe("legacy_polish_wrapper_parity", full_wrapper_equal=True)

    def test_900_codepoints_with_astral_characters_is_accepted_exactly(self):
        quote = "雨" * 898 + "🙂𠀀"
        self.selection = self.select(self.seed_reference("unicode900", body=quote))
        self.request["reference_selections"] = [self.selection]
        preview = self.preview()
        self.passages(prompt_material(preview["request"]), expected_quote=quote)
        self.assertEqual(preview["reference_passages"], prompt_material(preview["request"])["reference_passages"])
        self.assertEqual(len(quote), 900)
        self.assertGreater(len(quote.encode("utf-16-le")) // 2, 900)
        self.assertEqual(Provider.requests, [])
        self.observe("unicode900", codepoints=900, exact_quote_preserved=True)

    def test_901_codepoints_is_rejected_without_job_run_or_quota(self):
        reference = self.seed_reference("unicode901", body="雨" * 899 + "🙂𠀀")
        self.request["reference_selections"] = [self.select(reference)]
        self.assert_rejected_without_writes(self.request, "unicode901")

    def test_each_of_three_hashes_is_authoritative(self):
        for field in ("source_sha256", "section_hash", "source_hash"):
            with self.subTest(field=field):
                selected = {**self.selection, field: "0" * 64}
                self.assert_rejected_without_writes(
                    {**self.request, "reference_selections": [selected]}, "wrong_" + field,
                )

    def test_bool_offsets_are_rejected(self):
        for field in ("section_start", "section_end"):
            for value in (False, True):
                with self.subTest(field=field, value=value):
                    self.assert_rejected_without_writes({
                        **self.request, "reference_selections": [{**self.selection, field: value}],
                    }, "bool_" + field)

    def test_invalid_or_empty_ranges_are_rejected(self):
        for left, right in ((-1, 4), (4, 4), (12, 4), (0, len(BODY) + 1), (0.0, 4), (0, "4")):
            with self.subTest(start=left, end=right):
                self.assert_rejected_without_writes({
                    **self.request, "reference_selections": [{
                        **self.selection, "section_start": left, "section_end": right,
                    }],
                }, "invalid_range")

    def test_selection_container_and_required_fields_are_strict(self):
        for selected in (None, {}, "reference", [None], [{}]):
            with self.subTest(selected=selected):
                self.assert_rejected_without_writes(
                    {**self.request, "reference_selections": selected}, "invalid_selection_shape",
                )
        for field in self.selection:
            with self.subTest(missing=field):
                selected = {key: value for key, value in self.selection.items() if key != field}
                self.assert_rejected_without_writes(
                    {**self.request, "reference_selections": [selected]}, "missing_" + field,
                )

    def test_multiple_passages_cannot_bypass_total_limit(self):
        self.assert_rejected_without_writes({
            **self.request, "reference_selections": [self.selection, self.selection],
        }, "two_passages")

    def test_raw_client_quote_cannot_replace_server_quote(self):
        forged = {**self.selection, "source_quote": "客户端伪造正文，不能进入模型。"}
        self.assert_rejected_without_writes({
            **self.request, "reference_selections": [forged],
        }, "client_raw_quote")

    def test_reference_selection_only_allowed_for_continue_and_polish(self):
        for task in ("outline", "scene", "summary", "check", "brainstorm", "chapter_analysis", "semantic_review"):
            with self.subTest(task=task):
                self.assert_rejected_without_writes({**self.request, "task": task}, "unsupported_" + task)

    def test_foreign_project_reference_and_forged_pair_are_rejected(self):
        with server.connect() as conn:
            foreign = server.new_project(conn, {"title": "合成其他作品"})
        reference = self.seed_reference(
            "foreign", project=foreign["project"]["id"], branch=foreign["state"]["branch_id"],
        )
        selected = self.select(reference)
        self.assert_rejected_without_writes({
            **self.request, "reference_selections": [selected],
        }, "foreign_project")
        self.assert_rejected_without_writes({
            **self.request, "reference_selections": [{
                **selected, "document_id": self.selection["document_id"],
                "source_sha256": self.selection["source_sha256"],
            }],
        }, "foreign_section_local_document")

    def test_foreign_branch_reference_is_rejected(self):
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO branch VALUES(?,?,?,?,?,?,?)",
                ("synthetic-other-branch", PROJECT, BRANCH, None, None, "active", "{}"),
            )
        reference = self.seed_reference("otherbranch", branch="synthetic-other-branch")
        self.assert_rejected_without_writes({
            **self.request, "reference_selections": [self.select(reference)],
        }, "foreign_branch")

    def test_document_section_pair_must_match(self):
        selected = {
            **self.selection, "document_id": self.unconsumed["document_id"],
            "source_sha256": self.unconsumed["source_sha256"],
        }
        self.assert_rejected_without_writes({
            **self.request, "reference_selections": [selected],
        }, "unrelated_document_section_pair")

    def test_budget_keeps_reference_exact_and_counts_its_bytes(self):
        original = {key: value for key, value in self.request.items() if key != "reference_selections"}
        without, with_reference = self.preview(original), self.preview()
        self.passages(prompt_material(with_reference["request"]))
        difference = (with_reference["context_budget"]["estimated_utf8_bytes"]
                      - without["context_budget"]["estimated_utf8_bytes"])
        self.assertGreaterEqual(difference, len(QUOTE.encode("utf-8")))
        self.assertNotIn(
            self.selection["section_id"],
            [item.get("id") for item in with_reference["context_budget"]["truncation"]["removed"]],
        )
        self.observe("reference_budget", extra_context_bytes=difference, exact_quote_preserved=True)

    def test_mandatory_reference_budget_failure_is_before_any_write(self):
        reference = self.seed_reference("budget900", body="雨" * 900)
        self.request["reference_selections"] = [self.select(reference)]
        self.request["retrieval_profile"]["max_tokens"] = 6_000
        original = {key: value for key, value in self.request.items() if key != "reference_selections"}
        self.preview(original)
        self.assert_rejected_without_writes(self.request, "mandatory_reference_exceeds_budget")

    def test_unconsumed_reference_changes_do_not_invalidate_frozen_request(self):
        job = self.queue()
        _, consent, _ = self.assert_bound_passage(job)
        self.change_reference(self.unconsumed)
        self.change_reference(self.unconsumed, field="title")
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.assertEqual(Provider.requests, [consent["request_audit"]["payload"]])
        self.observe("unconsumed_reference_change", dispatch_success=True, exact_frozen_payload=True)

    def test_consumed_reference_body_change_blocks_dispatch(self):
        job = self.queue()
        self.assert_bound_passage(job)
        self.change_reference()
        self.assert_dispatch_blocked(job, "changed_consumed_section")

    def test_consumed_reference_source_metadata_change_blocks_dispatch(self):
        job = self.queue()
        self.assert_bound_passage(job)
        self.change_reference(field="source_json")
        self.assert_dispatch_blocked(job, "changed_source_metadata")

    def test_consumed_document_title_change_blocks_dispatch(self):
        job = self.queue()
        self.assert_bound_passage(job)
        self.change_reference(field="title")
        self.assert_dispatch_blocked(job, "changed_consumed_document_title")

    def test_missing_consumed_reference_blocks_dispatch(self):
        job = self.queue()
        self.assert_bound_passage(job)
        self.change_reference(remove=True)
        self.assert_dispatch_blocked(job, "removed_consumed_section")

    def test_changed_consumed_reference_blocks_job_and_run_retry_without_writes(self):
        job = self.queue()
        self.assert_bound_passage(job)
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.change_reference()
        self.assert_retries_blocked(job, "changed_consumed_section_retries")

    def test_missing_consumed_reference_blocks_job_and_run_retry_without_writes(self):
        job = self.queue()
        self.assert_bound_passage(job)
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.change_reference(remove=True)
        self.assert_retries_blocked(job, "missing_consumed_section_retries")

    def test_retry_preserves_original_reference_and_exact_payload(self):
        job = self.queue()
        parent_context, parent_consent, _ = self.assert_bound_passage(job)
        self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
        self.request["reference_selections"] = []
        with patch("server.start_ai_worker", return_value=False):
            status, retried = self.call(
                "POST", "/api/ai/jobs/" + job["id"] + "/retry", {"mode": "original"},
            )
        self.assertEqual(status, 202, retried)
        context, consent = self.frozen(retried)
        self.assertEqual(context["reference_passages"], parent_context["reference_passages"])
        self.assertEqual(consent["request_audit"]["payload"], parent_consent["request_audit"]["payload"])
        self.assertEqual(self.process(retried)["status"], "success")
        self.assertEqual(Provider.requests, [parent_consent["request_audit"]["payload"]])
        self.observe("retry_original_reference", reference_retained=True, exact_payload_retained=True)

    def test_changed_consumed_reference_blocks_candidate_revision_without_writes(self):
        job = self.queue()
        self.assert_bound_passage(job)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.change_reference()
        before, body, calls = self.snapshot(), self.body(), len(Provider.requests)
        status, result = self.revise(job, completed)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), calls)
        self.observe("stale_candidate_revision", http_status=status, database_unchanged=True, extra_provider_calls=0)

    def test_revision_inherits_reference_and_replay_reads_existing_child(self):
        job = self.queue()
        parent_context, parent_consent, _ = self.assert_bound_passage(job)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        status, revised = self.revise(job, completed)
        self.assertEqual(status, 202, revised)
        child = revised["job"]
        child_context, child_consent = self.frozen(child)
        self.assertEqual(child_context["reference_passages"], parent_context["reference_passages"])
        self.assertEqual(child_context["_material_source"], parent_context["_material_source"])
        self.assertEqual(child_consent["request_audit"]["payload"]["messages"][1],
                         parent_consent["request_audit"]["payload"]["messages"][1])
        self.change_reference()
        before, calls = self.snapshot(), len(Provider.requests)
        status, replay = self.revise(job, completed)
        self.assertEqual(status, 202, replay)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["job"]["id"], child["id"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(Provider.requests), calls)
        self.assert_dispatch_blocked(child, "stale_revision_child_dispatch")
        self.observe("revision_reference_inheritance", reference_retained=True, replay_only=True)

    def test_changed_consumed_reference_blocks_apply_without_writes(self):
        job = self.queue()
        self.assert_bound_passage(job)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.change_reference()
        before, body, calls = self.snapshot(), self.body(), len(Provider.requests)
        status, result = self.apply(job, completed)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), calls)
        self.observe("stale_apply", http_status=status, database_unchanged=True, body_unchanged=True, extra_provider_calls=0)

    def test_missing_consumed_reference_blocks_apply_without_writes(self):
        job = self.queue()
        self.assert_bound_passage(job)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        self.change_reference(remove=True)
        before, body = self.snapshot(), self.body()
        status, result = self.apply(job, completed)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.body(), body)
        self.assertEqual(len(Provider.requests), 1)
        self.observe("missing_reference_apply", http_status=status, database_unchanged=True, extra_provider_calls=0)

    def corrupt_reference_binding(self, job, scenario):
        context, _, _ = self.assert_bound_passage(job)
        if scenario == "missing_dependency":
            context["_material_source"]["dependencies"] = [
                item for item in context["_material_source"]["dependencies"]
                if item.get("kind") != "reference_section"
            ]
        elif scenario == "missing_passage":
            context.pop("reference_passages")
        elif scenario == "quote_mismatch":
            context["reference_passages"][0]["source_quote"] = "篡改后的合成片段。"
        else:
            raise AssertionError("Unknown binding corruption")
        with server.connect() as conn:
            conn.execute(
                "UPDATE app_ai_job SET context_json=? WHERE id=?",
                (server.json_text(context), job["id"]),
            )

    def test_reference_passage_without_source_binding_fails_closed(self):
        job = self.queue()
        self.corrupt_reference_binding(job, "missing_dependency")
        self.assert_dispatch_blocked(job, "reference_missing_binding")

    def test_orphan_reference_binding_without_passage_fails_closed(self):
        job = self.queue()
        self.corrupt_reference_binding(job, "missing_passage")
        self.assert_dispatch_blocked(job, "reference_orphan_binding")

    def test_frozen_passage_quote_mismatch_fails_closed(self):
        job = self.queue()
        self.corrupt_reference_binding(job, "quote_mismatch")
        self.assert_dispatch_blocked(job, "reference_quote_binding_mismatch")

    def test_incomplete_reference_bindings_block_both_retry_entries_without_writes(self):
        for scenario in ("missing_dependency", "missing_passage", "quote_mismatch"):
            with self.subTest(scenario=scenario):
                job = self.queue()
                self.assert_bound_passage(job)
                self.assertEqual(self.call("POST", "/api/ai/jobs/" + job["id"] + "/cancel", {})[0], 200)
                self.corrupt_reference_binding(job, scenario)
                self.assert_retries_blocked(job, "incomplete_reference_binding_retry_" + scenario)

    def test_incomplete_reference_bindings_block_revise_and_apply_without_writes(self):
        for scenario in ("missing_dependency", "missing_passage", "quote_mismatch"):
            with self.subTest(scenario=scenario):
                job = self.queue()
                self.assert_bound_passage(job)
                completed = self.process(job)
                self.assertEqual(completed["status"], "success", completed)
                self.corrupt_reference_binding(job, scenario)
                before, body, calls = self.snapshot(), self.body(), len(Provider.requests)
                status, public = self.call("GET", "/api/ai/jobs/" + job["id"])
                self.assertEqual(status, 200, public)
                self.assertTrue(public.get("materials_read_only"), public)
                self.assertEqual(self.snapshot(), before)
                outcomes = []
                for operation in ("revise", "apply"):
                    if operation == "revise":
                        status, rejected = self.revise(job, completed, request_id="synthetic-corrupt-" + scenario)
                    else:
                        status, rejected = self.apply(job, completed)
                    outcomes.append({"operation": operation, "status": status})
                    self.assertEqual(status, 400, rejected)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(self.body(), body)
                    self.assertEqual(len(Provider.requests), calls)
                self.observe(
                    "incomplete_reference_binding_actions_" + scenario,
                    outcomes=outcomes, database_unchanged=True, extra_provider_calls=0,
                )

    def test_reference_does_not_enter_story_facts_or_public_job_payload(self):
        preview = self.preview()
        material = prompt_material(preview["request"])
        self.passages(material)
        for name in ("confirmed_memory", "story_cards", "previous_chapters", "retrieved_chapters", "story_structure", "author_plans"):
            self.assertNotIn(CANARY, json.dumps(material.get(name), ensure_ascii=False))
        self.assertNotIn(CANARY, json.dumps(material.get("current_chapter"), ensure_ascii=False))
        before = self.canon_snapshot()
        job = self.queue()
        self.assert_bound_passage(job)
        completed = self.process(job)
        self.assertEqual(completed["status"], "success", completed)
        status, public = self.call("GET", "/api/ai/jobs/" + job["id"])
        self.assertEqual(status, 200, public)
        self.assertNotIn(CANARY, json.dumps(public, ensure_ascii=False))
        self.assertEqual(self.canon_snapshot(), before)
        self.assertNotIn(CANARY, self.body())
        self.observe("reference_fact_and_public_boundary", facts_unchanged=True, raw_reference_not_public=True)


def main():
    output = os.environ.get("MOYU_REFERENCE_PASSAGES_REPORT")
    target = Path(output) if output else None
    if target and target.exists():
        raise RuntimeError("Evidence exists; use a new report path")
    before, started = fingerprints(), datetime.now(timezone.utc).isoformat()
    Provider.total_calls = 0
    ReferencePassagesHTTP.observations = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReferencePassagesHTTP)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    after = fingerprints()
    report = {
        "passed": result.wasSuccessful() and before == after,
        "checks": result.testsRun, "cases": result.cases,
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": copy.deepcopy(ReferencePassagesHTTP.observations),
        "free_provider_calls": Provider.total_calls, "paid_provider_calls": 0,
        "source_files_sha256_before": before, "source_files_sha256_after": after,
        "source_unchanged": before == after,
        "scope": "temporary synthetic SQLite; real loopback HTTP; free local provider; no actual manuscript or credential",
        "claim_limit": "reference passage integrity and workflow behavior only; no literary-quality or competitiveness claim",
    }
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"cases", "observations"}}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
