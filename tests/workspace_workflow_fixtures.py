"""Actual HTTP regressions for independent books, atomic candidates and archives."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server
import moyu_backups


def main():
    with tempfile.TemporaryDirectory(prefix="moyu-workspace-") as tmp:
        server.DB_PATH = Path(tmp) / "book.sqlite3"
        server.init_db()
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{http.server_port}"

        def request(method, path, payload=None, project=None):
            raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
            headers = {"Content-Type": "application/json"}
            if project:
                headers["X-Moyu-Project"] = project
            req = urllib.request.Request(base + path, data=raw, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                return exc.code, json.load(exc)

        try:
            first = request("GET", "/api/state")[1]
            pid = first["project"]["id"]
            cid = first["chapters"][0]["id"]
            first["chapters"][0]["body"] = "甲😀乙。第一次红灯。第二次红灯。"
            assert request("PUT", "/api/state", first, pid)[0] == 200
            source_chapter = request("GET", "/api/state", project=pid)[1]["chapters"][0]
            source = server.candidate_source
            with server.connect() as conn:
                frozen = source(conn, "continue", source_chapter)
            status, generated = request("POST", "/api/ai", {"task": "continue", "chapter_id": cid,
                                                           "source": frozen}, pid)
            assert status == 200, generated
            application = {**frozen, "range": frozen["range"],
                           "replacement": "\n\n" + generated["text"], "decision": "accepted",
                           "candidate_hash": server.hash_text(generated["text"])}
            applied = request("POST", f"/api/ai/runs/{generated['ai_run_id']}/apply", application, pid)
            assert applied[0] == 200 and applied[1]["applied"], applied
            repeated = request("POST", f"/api/ai/runs/{generated['ai_run_id']}/apply", application, pid)
            assert repeated[0] == 200 and repeated[1]["idempotent"]
            body = repeated[1]["state"]["chapters"][0]["body"]
            assert body.count(generated["text"]) == 1
            changed = copy.deepcopy(application)
            changed["replacement"] += "额外文字"
            assert request("POST", f"/api/ai/runs/{generated['ai_run_id']}/apply", changed, pid)[0] == 409
            status, new = request("POST", "/api/projects", {"title": "第二本", "summary": "同名人物的新书"}, pid)
            assert status == 201, new
            other = new["project"]["id"]
            assert request("GET", "/api/state", project=other)[1]["chapters"][0]["body"] == ""
            assert request("POST", f"/api/ai/runs/{generated['ai_run_id']}/apply", application, other)[0] >= 400
            blueprint = {"premise": "两卷四章", "volumes": [
                {"id": "vol1", "title": "第一卷", "chapters": [
                    {"id": "plan1", "title": "相遇", "scenes": [{"id": "scene1", "title": "雨夜", "goal": "相遇"}]},
                    {"id": "plan2", "title": "选择"}]},
                {"id": "vol2", "title": "第二卷", "chapters": [
                    {"id": "plan3", "title": "代价"}, {"id": "plan4", "title": "回程"}]}]}
            saved = request("PUT", "/api/blueprint", {"expected_version": 0, "blueprint": blueprint}, other)
            assert saved[0] == 200, saved
            materialized = request("POST", "/api/blueprint/materialize", {"expected_version": 1}, other)
            assert materialized[0] == 200, materialized
            planned = [c for c in materialized[1]["state"]["chapters"] if c["plan_id"]]
            assert len(planned) == 4 and planned[0]["scenes"][0]["title"] == "雨夜"
            assert request("POST", "/api/blueprint/materialize", {"expected_version": 2}, other)[0] == 200
            assert len(request("GET", "/api/state", project=other)[1]["chapters"]) == 5
            original_planned = request("GET", "/api/state", project=other)[1]
            original_planned["chapters"][0]["body"] = "😀旧规划正文。更换蓝图以后仍保留。"
            assert request("PUT", "/api/state", original_planned, other)[0] == 200
            previous_chapters = request("GET", "/api/state", project=other)[1]["chapters"]
            previous_blueprint = request("GET", "/api/blueprint", project=other)[1]
            replacement_blueprint = {"premise": "新的两章规划", "volumes": [
                {"id": "replacement-volume", "title": "新卷", "chapters": [
                    {"id": "replacement-plan-1", "title": "雪路"},
                    {"id": "replacement-plan-2", "title": "封缄"}]}]}
            replaced = request("PUT", "/api/blueprint", {
                "expected_version": previous_blueprint["version"], "blueprint": replacement_blueprint}, other)
            assert replaced[0] == 200, replaced
            rebuilt = request("POST", "/api/blueprint/materialize", {
                "expected_version": replaced[1]["version"]}, other)
            assert rebuilt[0] == 200, rebuilt
            rebuilt_chapters = rebuilt[1]["state"]["chapters"]
            assert len(rebuilt_chapters) == len(previous_chapters) + 2
            assert [chapter["plan_id"] for chapter in rebuilt_chapters[:2]] == [
                "replacement-plan-1", "replacement-plan-2"]
            for previous in previous_chapters:
                assert next(c for c in rebuilt_chapters if c["id"] == previous["id"])["body"] == previous["body"]
            repeated_rebuild = request("POST", "/api/blueprint/materialize", {
                "expected_version": rebuilt[1]["version"]}, other)
            assert repeated_rebuild[0] == 200, repeated_rebuild
            assert [c["id"] for c in repeated_rebuild[1]["state"]["chapters"]] == [
                c["id"] for c in rebuilt_chapters]
            backup = request("POST", "/api/backups", {}, pid)
            assert backup[0] == 201 and backup[1]["verified"], backup
            backup_id = backup[1]["id"]
            package = Path(tmp) / "backups" / (backup_id + ".zip")
            snapshot = moyu_backups.read(package, pid)
            assert all(row.get("project_id", pid) == pid for rows in snapshot["payload"]["tables"].values() for row in rows)
            assert request("POST", f"/api/backups/{backup_id}/restore", {"confirm": True}, other)[0] == 404
            restored = request("POST", f"/api/backups/{backup_id}/restore", {"confirm": True}, pid)
            assert restored[0] == 201, restored
            recovered = restored[1]["project"]["id"]
            recovered_state = request("GET", "/api/state", project=recovered)[1]
            assert recovered_state["chapters"][0]["body"] == body
            assert recovered_state["chapters"][0]["id"] != cid
            assert request("GET", "/api/state", project=pid)[1]["chapters"][0]["body"] == body
            recovered_state["chapters"][0]["body"] += "\n恢复后继续写作"
            assert request("PUT", "/api/state", recovered_state, recovered)[0] == 200
            status, cloned = request("POST", f"/api/projects/{pid}/duplicate", {"title": "复制作品"}, pid)
            assert status == 201, cloned
            assert request("GET", "/api/state", project=cloned["project"]["id"])[1]["chapters"][0]["body"] == body
            assert request("PATCH", f"/api/projects/{other}", {"archived": True}, pid)[1]["archived"] == 1
            assert request("PATCH", f"/api/projects/{other}", {"archived": False}, pid)[1]["archived"] == 0
            title_cases = ["书" * size for size in (193, 194, 200)]
            title_cases += ["书" * 192 + "😀", "书" * 192 + "😀" + "卷" * 7]
            title_boundary_restores = 0
            for index, original_title in enumerate(title_cases):
                status, created = request("POST", "/api/projects", {"title": original_title}, pid)
                assert status == 201, created
                boundary_project = created["project"]["id"]
                boundary_state = request("GET", "/api/state", project=boundary_project)[1]
                boundary_body = f"标题边界{index}的原稿😀，备份恢复不能改字。"
                boundary_state["chapters"][0]["body"] = boundary_body
                assert request("PUT", "/api/state", boundary_state, boundary_project)[0] == 200
                status, boundary_backup = request("POST", "/api/backups", {}, boundary_project)
                assert status == 201 and boundary_backup["verified"], boundary_backup
                boundary_path = Path(tmp) / "backups" / (boundary_backup["id"] + ".zip")
                archive_bytes = boundary_path.read_bytes()
                archived_payload = moyu_backups.read(boundary_path, boundary_project)["payload"]
                assert archived_payload["tables"]["project"][0]["name"] == original_title
                status, boundary_restore = request(
                    "POST", f"/api/backups/{boundary_backup['id']}/restore",
                    {"confirm": True}, boundary_project)
                assert status == 201, boundary_restore
                restored_project = boundary_restore["project"]["id"]
                restored_state = request("GET", "/api/state", project=restored_project)[1]
                expected_title = original_title[:193] + " · 恢复副本"
                assert restored_state["project"]["title"] == expected_title
                assert len(restored_state["project"]["title"]) == 200
                assert restored_state["chapters"][0]["body"] == boundary_body
                restored_state["chapters"][0]["body"] += "\n第一次恢复后继续写。"
                assert request("PUT", "/api/state", restored_state, restored_project)[0] == 200
                assert request("GET", "/api/state", project=restored_project)[1]["chapters"][0]["body"] == restored_state["chapters"][0]["body"]
                original_after = request("GET", "/api/state", project=boundary_project)[1]
                assert original_after["project"]["title"] == original_title
                assert original_after["chapters"][0]["body"] == boundary_body
                assert boundary_path.read_bytes() == archive_bytes
                with server.connect() as conn:
                    assert moyu_backups.capture(conn, boundary_project) == archived_payload
                title_boundary_restores += 1
                if len(original_title) == 200:
                    status, second_backup = request("POST", "/api/backups", {}, restored_project)
                    assert status == 201 and second_backup["verified"], second_backup
                    status, second_restore = request(
                        "POST", f"/api/backups/{second_backup['id']}/restore",
                        {"confirm": True}, restored_project)
                    assert status == 201, second_restore
                    second_project = second_restore["project"]["id"]
                    second_state = request("GET", "/api/state", project=second_project)[1]
                    assert second_state["project"]["title"] == expected_title
                    assert second_state["chapters"][0]["body"] == restored_state["chapters"][0]["body"]
                    second_state["chapters"][0]["body"] += "\n第二次恢复后仍能写作😀。"
                    assert request("PUT", "/api/state", second_state, second_project)[0] == 200
                    assert request("GET", "/api/state", project=second_project)[1]["chapters"][0]["body"] == second_state["chapters"][0]["body"]
                    assert request("GET", "/api/state", project=restored_project)[1]["chapters"][0]["body"] == restored_state["chapters"][0]["body"]
                    title_boundary_restores += 1
            explicit_titles = ["  命名副本  ", " \t" + "名" * 200 + "\n ", "名" * 199 + "😀", ""]
            for explicit_title in explicit_titles:
                status, duplicate = request(
                    "POST", f"/api/projects/{pid}/duplicate", {"title": explicit_title}, pid)
                assert status == 201, duplicate
                expected_title = explicit_title.strip() if explicit_title else first["project"]["title"] + " · 恢复副本"
                assert duplicate["project"]["title"] == expected_title
                duplicate_state = request("GET", "/api/state", project=duplicate["project"]["id"])[1]
                assert duplicate_state["project"]["title"] == expected_title
                assert duplicate_state["chapters"][0]["body"] == body
            invalid_titles = [True, False, 123, [], {}, " \t\n ", "名" * 201, "名" * 200 + "😀"]
            with server.connect() as conn:
                before_invalid = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                       for table in ("project", "event_log"))
            for invalid_title in invalid_titles:
                status, rejected = request(
                    "POST", f"/api/projects/{pid}/duplicate", {"title": invalid_title}, pid)
                assert status == 400, (invalid_title, rejected)
                with server.connect() as conn:
                    after_invalid = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                          for table in ("project", "event_log"))
                assert after_invalid == before_invalid, (invalid_title, after_invalid, before_invalid)
            before = request("GET", "/api/state", project=pid)[1]
            with zipfile.ZipFile(package, "a") as archive:
                archive.writestr("unexpected.json", "{}")
            rejected = request("POST", f"/api/backups/{backup_id}/restore", {"confirm": True}, pid)
            assert rejected[0] >= 400
            assert request("GET", "/api/state", project=pid)[1] == before
            with server.connect() as conn:
                assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=3)
    print(f"workspace HTTP workflows: ok; {title_boundary_restores} title boundary restores, "
          f"{len(explicit_titles)} explicit names, {len(invalid_titles)} rejected names")


if __name__ == "__main__":
    main()
