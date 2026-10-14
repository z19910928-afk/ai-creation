"""Knowledge boundaries survive real card writes, legacy sync and recovery."""
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
import moyu_backups
import moyu_records
import server


class Provider(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.received.append(payload)
        raw = json.dumps({"model": "record-scope-fixture", "choices": [
            {"message": {"content": "林岚推开窗，只看见雨水从屋檐落下。"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


def main():
    checks = 0
    with tempfile.TemporaryDirectory(prefix="moyu-record-scope-") as tmp:
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        Provider.received = []
        environment = {"MOYU_AI_BASE_URL": f"http://127.0.0.1:{provider.server_port}/v1",
                       "MOYU_AI_MODEL": "record-scope-fixture", "MOYU_AI_API_KEY": "",
                       "MOYU_SECRET_KEY": "", "MOYU_CREDENTIALS_DIR": str(Path(tmp) / "credentials")}
        with patch.dict(os.environ, environment):
            server.DB_PATH = Path(tmp) / "book.sqlite3"
            server.init_db()
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{http.server_port}"

            def call(method, route, payload=None, project="default-project"):
                raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
                request = urllib.request.Request(base + route, method=method, data=raw,
                    headers={"Content-Type": "application/json", "X-Moyu-Project": project})
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, json.load(error)

            try:
                state = call("GET", "/api/state")[1]
                chapter_id = state["chapters"][0]["id"]
                state["chapters"][0].update(body="林岚守在北窗，看雨水淋湿石阶。" * 6, pov="林岚",
                                           chapter_goal="等待来信", scene_plan="北窗等待")
                state["records"] = [
                    {"id": "scope_lan", "type": "人物", "name": "林岚", "description": "守在北窗。"},
                    {"id": "scope_villain", "type": "人物", "name": "铁鹭", "description": "未现身的来客。"},
                    {"id": "scope_future", "type": "秘密", "name": "未来密语", "description": "第九章暗号：霜降灯灭。",
                     "knowledge_scope": {"available_from": "chapter-9"}, "available_from": "chapter-9"},
                    {"id": "scope_private", "type": "秘密", "name": "铁鹭密语", "description": "铁鹭私下暗号：灰燕归巢。",
                     "knowledge_scope": {"known_by": ["scope_villain"], "private": True}, "known_by": ["scope_villain"]},
                    {"id": "scope_public", "type": "设定", "name": "北窗", "description": "北窗的铜扣已经生锈。",
                     "knowledge_scope": {"known_by": ["scope_lan"], "available_from": 1}, "known_by": ["scope_lan"]},
                    {"id": "scope_plan", "type": "计划", "name": "未来转折", "description": "计划密语：纸舟沉没。",
                     "epistemic_mode": "author_plan"},
                    {"id": "scope_author", "type": "秘密", "name": "作者密语", "description": "作者密语：赤月无声。",
                     "knowledge_scope": {"private": True, "known_by": []}},
                    {"id": "scope_branch", "type": "设定", "name": "另一分支", "description": "另一分支密语：紫门洞开。",
                     "knowledge_scope": {"branches": ["alternate"]}},
                ]
                assert call("PUT", "/api/state", state)[0] == 200
                saved = call("GET", "/api/state")[1]
                for item in state["records"]:
                    actual = next(row for row in saved["records"] if row["id"] == item["id"])
                    for field in moyu_records.FIELDS & item.keys():
                        assert actual[field] == item[field], (field, actual)
                checks += 1

                def assert_materials(payload):
                    text = json.dumps(payload, ensure_ascii=False)
                    assert "北窗的铜扣已经生锈" in text
                    for secret in ("霜降灯灭", "灰燕归巢", "纸舟沉没", "赤月无声", "紫门洞开"):
                        assert secret not in text, secret

                request = {"task": "continue", "chapter_id": chapter_id,
                           "instruction": "北窗未来密语铁鹭密语作者密语",
                           "material_controls": {}}
                status, rejected_pin = call("POST", "/api/ai/prompt-preview", {
                    **request, "material_controls": {"records": {"scope_future": {"pin": True}}}})
                assert status == 400 and "scope" in rejected_pin["error"], rejected_pin
                checks += 1
                status, preview = call("POST", "/api/ai/prompt-preview", request)
                assert status == 200, preview
                assert_materials(preview)
                status, generated = call("POST", "/api/ai", request)
                assert status == 200, generated
                assert len(Provider.received) == 1
                assert_materials(Provider.received[0])
                checks += 2

                legacy = call("GET", "/api/state")[1]
                for item in legacy["records"]:
                    for field in moyu_records.FIELDS:
                        item.pop(field, None)
                assert call("PUT", "/api/state", legacy)[0] == 200
                assert_materials(call("POST", "/api/ai/prompt-preview", request)[1])
                checks += 1

                status, card = call("POST", "/api/records", {
                    "type": "秘密", "name": "新秘密", "description": "铜盒中封着蓝纸。",
                    "knowledge_scope": {"available_from": 8}, "known_by": ["scope_villain"]})
                assert status == 201, card
                status, edited = call("PATCH", "/api/records/" + card["id"], {"description": "铜盒里仍是蓝纸。"})
                assert status == 200 and edited["knowledge_scope"] == card["knowledge_scope"], edited
                assert edited["known_by"] == card["known_by"]
                with server.connect() as conn:
                    before = server.review_fingerprint(conn, chapter_id)
                status, edited = call("PATCH", "/api/records/" + card["id"], {
                    "knowledge_scope": {}, "known_by": [], "private": False})
                assert status == 200 and edited["knowledge_scope"] == {}, edited
                with server.connect() as conn:
                    assert server.review_fingerprint(conn, chapter_id) != before
                checks += 3

                invalid = [{"knowledge_scope": []}, {"known_by": "铁鹭"}, {"epistemic_mode": []},
                           {"knowledge_scope": {"known_by": [True]}}, {"available_from": True},
                           {"knowledge_scope": {"available_from": 9, "valid_end": 2}},
                           {"knowledge_scope": {"branches": "alternate"}}, {"knowledge_scope": {"private": "yes"}}]
                for declaration in invalid:
                    with server.connect() as conn:
                        before = tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                                       for table in ("entity", "app_entity_scope", "event_log"))
                    status, rejected = call("POST", "/api/records", {"name": "无效范围", **declaration})
                    assert status == 400, rejected
                    with server.connect() as conn:
                        assert before == tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                                               for table in ("entity", "app_entity_scope", "event_log"))
                checks += len(invalid)

                status, backup = call("POST", "/api/backups", {})
                assert status == 201 and backup["verified"], backup
                source = call("GET", "/api/state")[1]
                status, restored = call("POST", "/api/backups/" + backup["id"] + "/restore", {"confirm": True})
                assert status == 201, restored
                new_project = restored["project"]["id"]
                recovered = call("GET", "/api/state", project=new_project)[1]
                by_name = {item["name"]: item for item in recovered["records"]}
                assert by_name["铁鹭密语"]["known_by"] == [by_name["铁鹭"]["id"]]
                assert by_name["北窗"]["known_by"] == [by_name["林岚"]["id"]]
                assert by_name["未来密语"]["knowledge_scope"]["available_from"] == "chapter-9"
                recovered_request = {**request, "chapter_id": recovered["chapters"][0]["id"], "material_controls": {}}
                assert_materials(call("POST", "/api/ai/prompt-preview", recovered_request, new_project)[1])
                assert call("GET", "/api/state")[1]["records"] == source["records"]
                checks += 2

                # An archive made before the new projection table remains
                # restorable, without relaxing any saved row format checks.
                with server.connect() as conn:
                    old_payload = copy.deepcopy(moyu_backups.capture(conn, server.project_id()))
                    old_payload["tables"].pop("app_entity_scope")
                    restored_old = moyu_backups.transplant(conn, old_payload, "legacy_scope_archive", "旧格式副本")
                    assert restored_old["project_id"] == "legacy_scope_archive"
                checks += 1

                for broken in ("[]", "{}", '{"known_by":"scope_lan"}', '{"private":"yes"}', "broken-json"):
                    # An explicit empty scope is valid. All malformed saved
                    # boundaries fail closed before prompt construction.
                    if broken == "{}":
                        assert moyu_records.decode_scope(broken) == {}
                        continue
                    with server.connect() as conn:
                        conn.execute("UPDATE app_entity_scope SET scope_json=? WHERE entity_id=?",
                                     (broken, "scope_private"))
                        conn.commit()
                    status, unreadable = call("GET", "/api/state")
                    assert status == 500 and "可见范围损坏" in unreadable["error"]
                    assert call("POST", "/api/ai/prompt-preview", request)[0] == 400
                    with server.connect() as conn:
                        corrupt = copy.deepcopy(moyu_backups.capture(conn, server.project_id()))
                        before = conn.execute("SELECT COUNT(*) FROM project").fetchone()[0]
                        try:
                            moyu_backups.transplant(conn, corrupt, "broken_scope_archive", "损坏范围副本")
                        except ValueError:
                            pass
                        else:
                            raise AssertionError("corrupt boundaries must not restore as public")
                        assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == before
                    checks += 1
                with server.connect() as conn:
                    declaration = next(row for row in source["records"] if row["id"] == "scope_private")
                    moyu_records.write_scope(conn, "scope_private", moyu_records.normalize_scope(declaration), server.now())
                    conn.commit()
                assert len(Provider.received) == 1
            finally:
                http.shutdown()
                http.server_close()
                provider.shutdown()
                provider.server_close()
    print(json.dumps({"passed": True, "checks": checks, "provider_calls": len(Provider.received),
                      "scope": "isolated HTTP, free local provider, legacy sync and archive recovery"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
