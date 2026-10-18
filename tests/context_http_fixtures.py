#!/usr/bin/env python3
"""Capture actual local provider requests from temporary 30/100/300-chapter DBs.

The local provider returns fixed candidates. Corpus creation uses the real
database writer to avoid a giant import request. All retrieval, queueing,
provider transport and author decisions then use real HTTP endpoints.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_pipeline_fixtures import corpus  # noqa: E402
from backend.moyu_context import text_hash  # noqa: E402


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        user = payload["messages"][-1]["content"]
        context = json.loads(user.split("<manuscript>\n", 1)[1].split("\n</manuscript>", 1)[0])
        self.server.received.put(context)
        system = payload["messages"][0]["content"]
        current = context.get("current_chapter") or {}
        quote = current.get("body") or ""
        if "完整普通正文" in system:
            result = {
                "summary": {"text": quote, "evidence": [{"source_quote": quote}]},
                "handoff": {"text": "继续等待守灯人开门。", "evidence": [{"source_quote": quote}]},
                "candidates": [{
                    "kind": "event", "name": "等待开门", "note": quote,
                    "source_quote": quote, "epistemic_mode": "world_truth", "known_by": ["lan"],
                }],
            }
            output = json.dumps(result, ensure_ascii=False)
        elif "人物动机、因果、时间" in system:
            output = json.dumps({
                "findings": [{
                    "category": "style", "severity": "suggestion", "message": "可补一个具体的等待动作。",
                    "evidence": [{"source_chapter_id": current["id"], "source_quote": quote}],
                }],
                "uncertainties": ["这是固定模拟建议，不是文学质量评分。"],
            }, ensure_ascii=False)
            if "fixture_unseen_review_quote" in user:
                output = json.dumps({
                    "findings": [{
                        "category": "continuity", "severity": "possible", "message": "模拟恶意引用未来章节。",
                        "evidence": [{
                            "source_chapter_id": "chapter-29",
                            "source_quote": "真凶铁鹭就是林岚的父亲，暗号一共藏了十七年。",
                        }],
                    }], "uncertainties": [],
                }, ensure_ascii=False)
        else:
            output = "守灯人推开窗，把灯罩转向潮湿的石阶。"
        raw = json.dumps({
            "model": "local-context-fixture",
            "choices": [{"message": {"content": output}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 20},
        }, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


def request(base, method, path, payload=None, project=None):
    raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if project:
        headers["X-Moyu-Project"] = project
    req = urllib.request.Request(base + path, method=method, data=raw, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def wait_result(base, response, project=None):
    if "job" in response:
        job_id = response["job"]["id"]
    else:
        job_id = response.get("id") or response.get("job_id")
    if not job_id:
        return response
    for _ in range(200):
        status, item = request(base, "GET", "/api/ai/jobs/" + job_id, project=project)
        assert status == 200, item
        job = item.get("job") or item
        if job["status"] in {"succeeded", "failed", "canceled"}:
            assert job["status"] == "succeeded", job
            return job.get("result") or job.get("result_json") or job
        time.sleep(0.025)
    raise AssertionError("local fixture job did not finish")


def seed_database(server, database, size):
    server.DB_PATH = database
    server.init_db()
    state, structure, _, labels = corpus(size)
    with server.connect() as conn:
        for row in conn.execute("SELECT id FROM document_node WHERE kind='chapter'").fetchall():
            conn.execute("INSERT INTO app_deleted_node VALUES(?,?,?)", (row["id"], server.now(), "isolated test seed"))
        for record in state["records"]:
            conn.execute(
                "INSERT INTO entity(id,project_id,kind,canonical_name,aliases_json,status) VALUES(?,?,?,?,?,?)",
                (record["id"], server.project_id(), record["type"], record["name"], server.json_text(record["aliases"]), "active"),
            )
            conn.execute("INSERT INTO app_entity_meta VALUES(?,?,?)", (record["id"], record["description"], server.now()))
        for chapter in state["chapters"]:
            server.create_chapter(conn, chapter)
        for memory in state["memory"]:
            span_id = server.source_span_for_memory(conn, memory)
            assert span_id, memory["id"]
            payload = {
                "name": memory["name"], "note": memory.get("note") or memory["source_quote"],
                "source_chapter_id": memory["source_chapter_id"], "source_quote": memory["source_quote"],
                "known_by": memory.get("known_by") or ["lan"], "epistemic_mode": "world_truth",
            }
            conn.execute(
                "INSERT INTO candidate_memory(id,project_id,branch_id,source_span_id,entity_id,kind,payload_json,status,confidence,created_at,reviewed_at,reviewed_by,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (memory["id"], server.project_id(), server.branch_id(), span_id, "lan", "fact", server.json_text(payload), "accepted", 1, server.now(), server.now(), server.actor_id(), server.actor_id()),
            )
        for item in structure["character_states"]:
            if item["id"] != "villain-only":
                continue
            conn.execute(
                "INSERT INTO character_state(id,project_id,branch_id,entity_id,attribute,value_json,valid_start,knowledge_scope_json) VALUES(?,?,?,?,?,?,?,?)",
                (item["id"], server.project_id(), server.branch_id(), "villain", item["attribute"], server.json_text(item["value"]), item["valid_start"], server.json_text(item["knowledge_scope"])),
            )
        future = structure["timeline"][0]
        conn.execute(
            "INSERT INTO app_timeline_event(id,project_id,branch_id,title,event_time,chapter_id,entity_ids_json,summary,status,ordinal,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("future-time", server.project_id(), server.branch_id(), "未来揭示", "", future["chapter_id"], "[]", future["summary"], "occurred", 0, server.now(), server.now()),
        )
    return state, labels


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the measured JSON report to this path.")
    args = parser.parse_args()
    from backend import server
    provider = ThreadingHTTPServer(("127.0.0.1", 0), LocalProvider)
    provider.received = queue.Queue()
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    results = []
    workflow_checks = []
    try:
        for size in (30, 100, 300):
            with tempfile.TemporaryDirectory(prefix="moyu-context-http-") as directory:
                database = Path(directory) / "fixture.sqlite3"
                state, labels = seed_database(server, database, size)
                with server.connect() as conn:
                    expected_revisions = dict(conn.execute(
                        "SELECT document_node_id,id FROM document_revision ORDER BY rowid",
                    ).fetchall())
                app_port = available_port()
                environment = {
                    **os.environ, "MOYU_DB": str(database), "MOYU_HOST": "127.0.0.1",
                    "MOYU_PORT": str(app_port), "MOYU_AI_BASE_URL": f"http://127.0.0.1:{provider.server_address[1]}/v1",
                    "MOYU_AI_MODEL": "local-context-fixture", "MOYU_AI_API_KEY": "", "MOYU_AI_DAILY_QUOTA": "0",
                }
                with (Path(directory) / "server.log").open("w", encoding="utf-8") as log:
                    process = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=environment, stdout=log, stderr=log)
                    base = f"http://127.0.0.1:{app_port}"
                    try:
                        for _ in range(150):
                            try:
                                status, health = request(base, "GET", "/api/health")
                                if status == 200:
                                    break
                            except OSError:
                                if process.poll() is not None:
                                    raise AssertionError((Path(directory) / "server.log").read_text())
                                time.sleep(0.025)
                        else:
                            raise AssertionError("temporary application did not start")
                        measured = {}
                        for top_k in (1, 5, 10):
                            missed, illegal, durations, observations = [], [], [], []
                            cited, correct = 0, 0
                            for label in labels:
                                started = time.perf_counter()
                                status, response = request(base, "POST", "/api/ai", {
                                    "task": "continue", "chapter_id": f"chapter-{size - 2}", "instruction": label["query"],
                                    "retrieval_profile": {"chapter_top_k": top_k, "recent_window": 1, "memory_top_k": 3, "record_top_k": 5},
                                })
                                assert status in {200, 202}, response
                                wait_result(base, response)
                                context = provider.received.get(timeout=5)
                                durations.append(round(time.perf_counter() - started, 6))
                                ranked = context["retrieved_chapters"]
                                rank = next((index + 1 for index, item in enumerate(ranked)
                                             if item["id"] == f"chapter-{label['chapter']}" and label["quote"] in item["excerpt"]), None)
                                if rank is None:
                                    missed.append(label["quote"])
                                serialized = json.dumps(context, ensure_ascii=False)
                                if "真凶铁鹭就是林岚的父亲" in serialized or any(item["id"] == "villain-only" for item in context["story_structure"]["character_states"]):
                                    illegal.append(label["query"])
                                for item in ranked + context["previous_chapters"]:
                                    cited += 1
                                    source_body = state["chapters"][item["chapter"] - 1]["body"]
                                    span = item["source_range"]
                                    accurate = (
                                        source_body[span["start"]:span["end"]] == item["excerpt"]
                                        and item["source_revision_id"] == expected_revisions[item["id"]]
                                        and item["evidence"]["source_quote"] == item["excerpt"]
                                        and item["evidence"]["source_range"] == span
                                        and item["evidence"]["source_hash"] == text_hash(item["excerpt"])
                                        and item["evidence"]["chapter_hash"] == text_hash(source_body)
                                        and item["chapter"] < size - 2
                                    )
                                    correct += int(accurate)
                                    assert accurate, item
                                assert context["context_budget"]["author_instruction_used"], context["context_budget"]
                                actual_material_bytes = len(json.dumps(context, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))
                                assert context["context_budget"]["estimated_utf8_bytes"] == actual_material_bytes, context["context_budget"]
                                observations.append({
                                    "query": label["query"], "gold_chapter": label["chapter"],
                                    "gold_quote_hash": text_hash(label["quote"]), "rank_in_sent_packet": rank,
                                    "actual_ranked_passages_sent": len(ranked),
                                    "actual_total_chapter_passages_sent": len(ranked) + len(context["previous_chapters"]),
                                    "context_utf8_bytes": context["context_budget"]["estimated_utf8_bytes"],
                                    "actual_material_utf8_bytes": actual_material_bytes,
                                    "context_budget_max": context["context_budget"]["max_tokens"],
                                    "elapsed_seconds": durations[-1],
                                })
                            assert not illegal, {"chapters": size, "top_k": top_k, "illegal": illegal}
                            if top_k >= 5:
                                assert not missed, {"chapters": size, "top_k": top_k, "missed": missed}
                            measured[str(top_k)] = {
                                "recall": (len(labels) - len(missed)) / len(labels),
                                "gold_passages": len(labels), "missed_passages": missed,
                                "citation_accuracy": correct / cited if cited else None,
                                "citation_count": cited, "correct_citations": correct,
                                "illegal_materials": len(illegal),
                                "end_to_end_simulated_provider_seconds": {
                                    "p50": percentile(durations, 0.5), "p95": percentile(durations, 0.95),
                                    "min": min(durations), "max": max(durations), "samples": durations,
                                },
                                "observations": observations,
                            }
                        results.append({
                            "chapters": size, "actual_provider_requests": len(labels) * 3,
                            "fixed_labelled_queries": len(labels),
                            "source_characters": sum(len(item["body"]) for item in state["chapters"]),
                            "recall_at_k": measured,
                        })
                        if size == 30:
                            current_id = "chapter-28"
                            status, response = request(base, "POST", "/api/ai", {"task": "chapter_analysis", "chapter_id": current_id, "response_format": "json"})
                            assert status in {200, 202}, response
                            wait_result(base, response)
                            provider.received.get(timeout=5)
                            status, listing = request(base, "GET", "/api/chapter-analysis?chapter_id=" + current_id)
                            assert status == 200 and listing["analyses"], listing
                            analysis = listing["analyses"][0]
                            confirmation = {
                                "source_revision_id": analysis["source_revision_id"], "chapter_hash": analysis["chapter_hash"],
                                "confirm_summary": True, "confirm_handoff": True,
                                "decisions": [{"candidate_id": analysis["candidates"][0]["id"], "decision": "accepted"}],
                            }
                            status, applied = request(base, "POST", f"/api/chapter-analysis/{analysis['id']}/confirm", confirmation)
                            assert status == 200, applied
                            status, repeated = request(base, "POST", f"/api/chapter-analysis/{analysis['id']}/confirm", confirmation)
                            assert status == 200 and repeated["analysis"]["confirmed_candidates"] == [], repeated
                            status, next_chapter = request(base, "POST", "/api/chapters", {"title": "接续", "body": "林岚还在门外。", "pov": "林岚"})
                            assert status == 201, next_chapter
                            status, response = request(base, "POST", "/api/ai", {"task": "continue", "chapter_id": next_chapter["id"], "instruction": "接续等待开门"})
                            assert status in {200, 202}, response
                            wait_result(base, response)
                            actual_next = provider.received.get(timeout=5)
                            assert any(item.get("source_chapter_id") == current_id for item in actual_next["confirmed_memory"]), actual_next["confirmed_memory"]
                            status, response = request(base, "POST", "/api/ai", {"task": "semantic_review", "chapter_id": current_id, "response_format": "json"})
                            assert status in {200, 202}, response
                            wait_result(base, response)
                            provider.received.get(timeout=5)
                            status, review_list = request(base, "GET", "/api/story-reviews?chapter_id=" + current_id)
                            assert status == 200 and review_list["reviews"], review_list
                            review = review_list["reviews"][0]
                            assert review["evidence_scope_verified"], review
                            status, rejected_response = request(base, "POST", "/api/ai", {
                                "task": "semantic_review", "chapter_id": current_id, "response_format": "json",
                                "instruction": "fixture_unseen_review_quote",
                                "retrieval_profile": {"chapter_top_k": 0, "recent_window": 0, "memory_top_k": 0, "record_top_k": 0},
                            })
                            assert status in {200, 202, 400, 409, 502}, rejected_response
                            rejected_job_id = (rejected_response.get("job") or {}).get("id")
                            if status >= 400:
                                assert "sent to the model" in str(rejected_response.get("error", "")), rejected_response
                            elif rejected_job_id:
                                for _ in range(200):
                                    job_status, failure = request(base, "GET", "/api/ai/jobs/" + rejected_job_id)
                                    failed_job = failure.get("job") or failure
                                    assert job_status == 200, failure
                                    if failed_job["status"] in {"failed", "succeeded", "canceled"}:
                                        assert failed_job["status"] == "failed", failure
                                        break
                                    time.sleep(0.025)
                                else:
                                    raise AssertionError("unseen-evidence review did not fail")
                            else:
                                assert rejected_response.get("safe_to_apply") is False or rejected_response.get("status") == "failed", rejected_response
                            provider.received.get(timeout=5)
                            status, unchanged_reviews = request(base, "GET", "/api/story-reviews?chapter_id=" + current_id)
                            assert status == 200 and len(unchanged_reviews["reviews"]) == len(review_list["reviews"]), unchanged_reviews
                            status, handled = request(base, "POST", f"/api/story-reviews/{review['id']}/issues/{review['findings'][0]['id']}/decision", {"decision": "accepted"})
                            assert status == 200 and handled["review"]["status"] == "needs_revision", handled
                            status, revised = request(base, "PATCH", "/api/chapters/" + current_id, {"body": "林岚离开了北塔，返回渡口。"})
                            assert status == 200, revised
                            status, revised_state = request(base, "GET", "/api/state")
                            assert status == 200, revised_state
                            revised_chapter = next(item for item in revised_state["chapters"] if item["id"] == current_id)
                            assert revised_chapter["recap"] == "" and revised_chapter["next_brief"] == "", revised_chapter
                            workflow_checks.append({"name": "body-only PATCH clears obsolete automatic summary and handoff", "passed": True})
                            status, stale = request(base, "GET", "/api/chapter-analysis?chapter_id=" + current_id)
                            assert status == 200 and stale["analyses"][0]["status"] == "stale", stale
                            status, stale_review = request(base, "GET", "/api/story-reviews?chapter_id=" + current_id)
                            assert status == 200 and stale_review["reviews"][0]["status"] == "stale", stale_review
                            status, fresh_book = request(base, "POST", "/api/projects", {"title": "摘要保存隔离作品"})
                            assert status == 201, fresh_book
                            summary_project = fresh_book["project"]["id"]
                            for author_handoff in ("", "作者要求下一章从守灯人归来开始。"):
                                status, created = request(base, "POST", "/api/chapters", {
                                    "title": "全量保存摘要失效", "body": "林岚把钥匙交给守灯人。", "pov": "林岚",
                                }, project=summary_project)
                                assert status == 201, created
                                saved_id = created["id"]
                                status, response = request(base, "POST", "/api/ai", {
                                    "task": "chapter_analysis", "chapter_id": saved_id, "response_format": "json",
                                }, project=summary_project)
                                assert status in {200, 202}, response
                                wait_result(base, response, project=summary_project)
                                provider.received.get(timeout=5)
                                status, listing = request(base, "GET", "/api/chapter-analysis?chapter_id=" + saved_id, project=summary_project)
                                assert status == 200 and listing["analyses"], listing
                                saved_analysis = listing["analyses"][0]
                                status, confirmed = request(base, "POST", f"/api/chapter-analysis/{saved_analysis['id']}/confirm", {
                                    "source_revision_id": saved_analysis["source_revision_id"],
                                    "chapter_hash": saved_analysis["chapter_hash"],
                                    "confirm_summary": True, "confirm_handoff": True, "decisions": [],
                                }, project=summary_project)
                                assert status == 200, confirmed
                                status, full_state = request(base, "GET", "/api/state", project=summary_project)
                                assert status == 200, full_state
                                chapter = next(item for item in full_state["chapters"] if item["id"] == saved_id)
                                assert chapter["recap"] and chapter["next_brief"], chapter
                                chapter["body"] = "林岚把钥匙留在口袋里，独自离开渡口。"
                                if author_handoff:
                                    chapter["next_brief"] = author_handoff
                                full_state["full_state"] = True
                                status, saved_state = request(base, "PUT", "/api/state", full_state, project=summary_project)
                                assert status == 200, saved_state
                                saved_chapter = next(item for item in saved_state["chapters"] if item["id"] == saved_id)
                                assert saved_chapter["recap"] == "", saved_chapter
                                assert saved_chapter["next_brief"] == author_handoff, saved_chapter
                                status, persisted_state = request(base, "GET", "/api/state", project=summary_project)
                                assert status == 200, persisted_state
                                persisted_chapter = next(item for item in persisted_state["chapters"] if item["id"] == saved_id)
                                assert persisted_chapter["recap"] == "" and persisted_chapter["next_brief"] == author_handoff, persisted_chapter
                                status, stale = request(base, "GET", "/api/chapter-analysis?chapter_id=" + saved_id, project=summary_project)
                                assert status == 200 and stale["analyses"][0]["status"] == "stale", stale
                                workflow_checks.append({
                                    "name": "full-state PUT preserves simultaneous author handoff" if author_handoff else "full-state PUT cannot restore obsolete generated fields",
                                    "passed": True,
                                })
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
    finally:
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=5)
    report = {
        "passed": True,
        "scope": "real local HTTP and actual captured provider material; simulated model; temporary DBs",
        "dataset": "fixed synthetic night-harbor prose; 30/100/300 chapters, five labelled ordinary sentences in chapter middles",
        "method": "K controls actual ranked chapters; recall measures gold exact passage retained after final budget and provider serialization",
        "limits": [
            "These scores apply only to the supplied fixed corpus and five labelled queries per size.",
            "Local lexical retrieval and exact source grounding are measured; literary quality and model knowledge are not.",
            "K is a maximum; the conservative byte budget may send fewer passages.",
            "Latency includes real HTTP, SQLite and a zero-cost simulated model response; it is not production-provider latency.",
            "Token counts are a conservative byte estimate, not exact provider tokenization.",
        ],
        "results": results,
        "workflow_checks": workflow_checks,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
