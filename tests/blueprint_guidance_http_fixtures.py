"""Real bounded blueprint consumption, POV filtering and stale-plan protection."""
from __future__ import annotations

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Provider(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append(payload)
        text = (json.dumps({"findings": [], "uncertainties": []})
                if "人物动机、因果、时间" in payload["messages"][0]["content"]
                else "林岚把铜扣攥在掌心，等到窗外的脚步停下才开口。")
        raw = encode({"model": "guidance-fixture", "choices": [
            {"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


class App(server.Handler):
    def log_message(self, *_args):
        pass


def material(payload):
    user = payload["messages"][-1]["content"]
    return json.loads(user.split("<manuscript>\n", 1)[1].split("\n</manuscript>", 1)[0])


def main():
    checks = 0
    with tempfile.TemporaryDirectory(prefix="moyu-guidance-") as tmp:
        environment = {"MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": "", "MOYU_AI_API_KEY": "",
                       "MOYU_SECRET_KEY": "", "MOYU_CREDENTIALS_DIR": str(Path(tmp) / "credentials")}
        with patch.dict(os.environ, environment):
            server.DB_PATH = Path(tmp) / "book.sqlite3"
            server.init_db()
            provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
            http = ThreadingHTTPServer(("127.0.0.1", 0), App)
            Provider.calls = []
            for instance in (provider, http):
                threading.Thread(target=instance.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{http.server_port}"

            def call(method, route, value=None, project="default-project"):
                request = urllib.request.Request(base + route, method=method,
                    data=encode(value) if value is not None else None,
                    headers={"Content-Type": "application/json", "X-Moyu-Project": project})
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, json.load(error)

            try:
                chapter_id = call("GET", "/api/state")[1]["chapters"][0]["id"]
                status, minimal = call("POST", "/api/ai/prompt-preview", {
                    "task": "continue", "chapter_id": chapter_id,
                    "retrieval_profile": {"max_tokens": 4_000, "chapter_top_k": 0, "recent_window": 0,
                                          "memory_top_k": 0, "record_top_k": 0}})
                assert status == 200, minimal
                minimal_material = material({"messages": minimal["messages"]})
                assert "author_guidance" not in minimal_material and "_guidance_source" not in minimal_material
                assert minimal_material["context_budget"]["estimated_utf8_bytes"] == len(encode(minimal_material))
                assert minimal_material["context_budget"]["estimated_tokens"] <= 4_000
                assert not Provider.calls
                checks += 1

                assert call("PUT", "/api/ai/settings", {
                    "base_url": f"http://127.0.0.1:{provider.server_port}/v1",
                    "model": "guidance-fixture", "api_key": "", "daily_limit": 20,
                    "parameters": {"temperature": 0, "max_tokens": 800}})[0] == 200
                blueprint = {
                    "premise": "雨夜窗前等待的选择", "genre": "悬疑",
                    "audience": "AUDIENCE_GUIDE", "tone": "TONE_GUIDE", "contract": "CONTRACT_GUIDE",
                    "outline": "FUTURE_OUTLINE_SECRET",
                    "volumes": [
                        {"id": "guide_volume", "title": "窗前", "summary": "CURRENT_VOLUME_GUIDE",
                         "chapters": [
                             {"id": "guide_previous", "title": "章前", "goal": "交接钥匙", "pov": "林岚", "scenes": []},
                             {"id": "guide_current", "title": "窗边", "goal": "CURRENT_GOAL", "pov": "林岚",
                              "hook": "CURRENT_HOOK", "scenes": [
                                  {"id": "guide_scene", "title": "窗前等候", "goal": "CURRENT_SCENE_GOAL",
                                   "location": "CURRENT_LOCATION", "pov": "林岚"},
                                  {"id": "guide_enemy_scene", "title": "敌方独处", "goal": "ENEMY_SCENE_SECRET",
                                   "location": "ENEMY_LOCATION_SECRET", "pov": "铁鹭"}]},
                             {"id": "guide_future", "title": "FUTURE_CHAPTER_SECRET",
                              "goal": "FUTURE_GOAL_SECRET", "pov": "铁鹭", "scenes": []}]},
                        {"id": "guide_future_volume", "title": "FUTURE_VOLUME_SECRET",
                         "summary": "FUTURE_VOLUME_SUMMARY_SECRET", "chapters": []}],
                }
                status, saved = call("PUT", "/api/blueprint", {"expected_version": 0, "blueprint": blueprint})
                assert status == 200
                status, built = call("POST", "/api/blueprint/materialize", {"expected_version": saved["version"]})
                assert status == 200, built
                current = next(item for item in built["state"]["chapters"] if item.get("plan_id") == "guide_current")
                previous = next(item for item in built["state"]["chapters"] if item.get("plan_id") == "guide_previous")
                chapter_id = current["id"]
                assert call("PATCH", "/api/chapters/" + previous["id"], {"next_brief": "PREVIOUS_HANDOFF_GUIDE"})[0] == 200
                assert call("PATCH", "/api/chapters/" + chapter_id, {
                    "body": "林岚抬起手，扣住北窗的铜扣。" * 8})[0] == 200
                checks += 1

                expected = ("AUDIENCE_GUIDE", "TONE_GUIDE", "CONTRACT_GUIDE", "CURRENT_VOLUME_GUIDE",
                            "CURRENT_SCENE_GOAL", "CURRENT_LOCATION", "CURRENT_GOAL", "PREVIOUS_HANDOFF_GUIDE")
                forbidden = ("FUTURE_OUTLINE_SECRET", "FUTURE_CHAPTER_SECRET", "FUTURE_GOAL_SECRET",
                             "FUTURE_VOLUME_SECRET", "FUTURE_VOLUME_SUMMARY_SECRET",
                             "ENEMY_SCENE_SECRET", "ENEMY_LOCATION_SECRET")
                queued_jobs = []
                for task in ("continue", "polish", "semantic_review"):
                    request = {"task": task, "chapter_id": chapter_id, "instruction": "按已保存的本章指南处理。",
                               "response_format": "json" if task == "semantic_review" else "text"}
                    status, preview = call("POST", "/api/ai/prompt-preview", request)
                    assert status == 200, preview
                    status, queued = call("POST", "/api/ai/queue", {**request, "defer": True})
                    assert status == 202, queued
                    status, result = call("POST", "/api/ai/jobs/" + queued["id"] + "/process", {})
                    assert status == 200 and result["status"] == "success", result
                    actual = Provider.calls[-1]
                    assert actual["messages"] == preview["messages"]
                    serialized = actual["messages"][-1]["content"]
                    assert all(marker in serialized for marker in expected)
                    assert all(marker not in serialized for marker in forbidden)
                    context = material(actual)
                    guide = context["author_guidance"]
                    assert guide["epistemic_mode"] == "author_plan"
                    assert guide["use"] == "writing_guidance_not_occurred_not_character_knowledge"
                    assert guide["scenes_outside_current_pov"] == 1 and len(guide["scenes"]) == 1
                    assert "林岚" == guide["scenes"][0]["pov"]
                    assert context["context_budget"]["estimated_tokens"] <= context["context_budget"]["max_tokens"]
                    queued_jobs.append(queued)
                    checks += 1

                # POV aliases select the same stable scene, without exposing
                # the other viewpoint through the generated scene_plan text.
                state = call("GET", "/api/state")[1]
                assert call("POST", "/api/records", {"type": "人物", "name": "林岚", "aliases": ["岚"],
                                                   "description": "守窗的人。"})[0] == 201
                assert call("PATCH", "/api/chapters/" + chapter_id, {"pov": "岚"})[0] == 200
                status, preview = call("POST", "/api/ai/prompt-preview", {"task": "continue", "chapter_id": chapter_id})
                assert status == 200 and "CURRENT_LOCATION" in preview["user"]
                assert all(marker not in preview["user"] for marker in forbidden)
                checks += 1

                # A source-scoped candidate cannot be applied or dispatched
                # after either the blueprint or its chapter projection changes.
                request = {"task": "continue", "chapter_id": chapter_id, "defer": True}
                status, successful = call("POST", "/api/ai/queue", request)
                assert status == 202
                status, result = call("POST", "/api/ai/jobs/" + successful["id"] + "/process", {})
                assert status == 200 and result["status"] == "success"
                status, waiting = call("POST", "/api/ai/queue", request)
                assert status == 202
                old_calls = len(Provider.calls)
                old_body = call("GET", "/api/state")[1]
                old_body = next(item["body"] for item in old_body["chapters"] if item["id"] == chapter_id)
                current_blueprint = call("GET", "/api/blueprint")[1]
                updated = copy.deepcopy(current_blueprint["blueprint"])
                updated["tone"] = "REVISED_TONE_GUIDE"
                assert call("PUT", "/api/blueprint", {"expected_version": current_blueprint["version"],
                                                    "blueprint": updated})[0] == 200
                status, canceled = call("POST", "/api/ai/jobs/" + waiting["id"] + "/process", {})
                assert status == 200 and canceled["status"] == "failed", canceled
                assert len(Provider.calls) == old_calls
                with server.connect() as conn:
                    frozen = json.loads(conn.execute("SELECT context_json FROM app_ai_job WHERE id=?",
                                                    (successful["id"],)).fetchone()[0])
                    source = frozen["_source"]
                status, rejected = call("POST", "/api/ai/runs/" + successful["ai_run_id"] + "/apply", {
                    **source, "range": source["range"], "replacement": "\n\n" + result["result"]["text"],
                    "decision": "accepted"})
                assert status in (400, 409) and "蓝图" in rejected["error"], rejected
                current_body = next(item["body"] for item in call("GET", "/api/state")[1]["chapters"]
                                    if item["id"] == chapter_id)
                assert old_body == current_body
                checks += 2

                status, waiting = call("POST", "/api/ai/queue", request)
                assert status == 202
                assert call("PATCH", "/api/chapters/" + chapter_id, {"chapter_goal": "更新后的本章目标"})[0] == 200
                status, canceled = call("POST", "/api/ai/jobs/" + waiting["id"] + "/process", {})
                assert status == 200 and canceled["status"] == "failed"
                assert len(Provider.calls) == old_calls
                checks += 1

                # Large mandatory author guidelines are rejected before a
                # model request instead of falsely claiming a bounded prompt.
                latest = call("GET", "/api/blueprint")[1]
                huge = copy.deepcopy(latest["blueprint"])
                huge["contract"] = "界" * 20000
                assert call("PUT", "/api/blueprint", {"expected_version": latest["version"], "blueprint": huge})[0] == 200
                status, rejected = call("POST", "/api/ai/prompt-preview", {
                    "task": "continue", "chapter_id": chapter_id, "retrieval_profile": {"max_tokens": 4000}})
                assert status == 400 and "超出本次材料容量" in rejected["error"], rejected
                assert len(Provider.calls) == old_calls
                checks += 1
            finally:
                for instance in (http, provider):
                    instance.shutdown()
                    instance.server_close()
    print(json.dumps({"passed": True, "checks": checks, "free_provider_calls": len(Provider.calls),
                      "paid_provider_calls": 0, "scope": "isolated real HTTP; frozen guidance, POV and conservative budget"}))


if __name__ == "__main__":
    main()
