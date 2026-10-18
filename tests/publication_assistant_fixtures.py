#!/usr/bin/env python3
"""Actual HTTP regressions for exact publication and confirmed assistant jobs."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import moyu_import
from backend import moyu_backups
from backend import moyu_provider
from backend import moyu_publication
from backend import moyu_settings
from backend import server


BODY_A = (
    "\ufeff  林岚把旧信放回柜里。\r\n\r\n\t"
    "𠀀字、🙂、<标签>&引号“原样”都在。  \r\n"
    "## 正文中的标题不是新章节\r\n"
    "```text\r\n保留代码块里的空格  \r\n```\r\n"
    "船舱的灯灭了一次，他记住每一道回声。旅人仍在等着那封迟来的信。\r\n  "
)
BODY_B = (
    "\n\t守灯人说，下一班船会在黎明前到港。\n\n"
    "两个相同章名仍然是两章，不能拼接或重复。北塔还留着昨夜的潮气，"
    "门上的刻痕没有改变。林岚数完台阶，才发现铜环不在原来的位置。  \n\n"
)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def source_marker(value, digest=None):
    raw = json_bytes(value)
    return (
        "<!-- moyu-manuscript-v1 "
        + (digest or hashlib.sha256(raw).hexdigest())
        + " " + base64.b64encode(raw).decode("ascii") + " -->"
    ).encode("ascii")


def rewrite_epub(raw, changes=None, removed=()):
    changes = changes or {}
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(output, "w") as archive:
        for info in original.infolist():
            if info.filename not in removed:
                archive.writestr(info, changes.get(info.filename, original.read(info.filename)))
        for name, content in changes.items():
            if name not in original.namelist():
                archive.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def third_party_epub():
    output = io.BytesIO()
    files = {
        "META-INF/container.xml": (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="book/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        ),
        "book/content.opf": (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>外部电子书</dc:title></metadata>'
            '<manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>'
        ),
        "book/chapter.xhtml": (
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章</title></head>'
            '<body><h1>第一章</h1><p>外部正文的可读文字。</p>'
            '<img src="picture.png" alt="港口的旧塔"/><script>不得导入脚本正文</script></body></html>'
        ),
    }
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, text in files.items():
            archive.writestr(name, text.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


class PublicationAssistantFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="moyu-publication-assistant-")
        cls.root = Path(cls.directory.name)
        cls.environment = patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "", "MOYU_AI_API_KEY": "", "MOYU_AI_MODEL": "",
            "MOYU_AI_DAILY_QUOTA": "0", "MOYU_CREDENTIALS_DIR": str(cls.root / "private-keys"),
        })
        cls.environment.start()
        cls.original_db = server.DB_PATH
        server.DB_PATH = cls.root / "book.sqlite3"
        server._IMPORT_PREVIEWS.clear()
        server.init_db()
        cls.log_patch = patch.object(server.Handler, "log_message", lambda *_args: None)
        cls.log_patch.start()
        cls.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=3)
        cls.log_patch.stop()
        server.DB_PATH = cls.original_db
        cls.environment.stop()
        cls.directory.cleanup()

    def setUp(self):
        self.sentinels = [
            patch.object(server, "generate_ai", side_effect=AssertionError("unexpected generation")),
            patch.object(server, "start_ai_worker", side_effect=AssertionError("unexpected worker")),
            patch.object(moyu_provider, "call_chat_completion", side_effect=AssertionError("unexpected provider")),
            patch.object(moyu_settings, "call_chat_completion", side_effect=AssertionError("unexpected diagnostic")),
        ]
        self.mocks = [sentinel.start() for sentinel in self.sentinels]
        self.addCleanup(self.stop_sentinels)
        status, created = self.request("POST", "/api/projects", {
            "title": "逐字回读验证", "summary": "旅人回到港口，重新寻找旧信的主人。", "genre": "故事",
        })
        self.assertEqual(status, 201, created)
        self.project = created["project"]["id"]
        self.branch = created["project"]["branch_id"]
        state = self.request("GET", "/api/state", project=self.project)[1]
        self.chapter = state["chapters"][0]["id"]
        status, result = self.request("PATCH", "/api/chapters/" + self.chapter, {
            "title": "同名章节", "body": BODY_A, "status": "完成",
            "chapter_goal": "找到旧信", "hook": "铜环失踪", "recap": "旅人回到港口。",
        }, self.project)
        self.assertEqual(status, 200, result)
        status, result = self.request("POST", "/api/chapters", {
            "title": "同名章节", "body": BODY_B, "status": "完成",
            "chapter_goal": "找到守灯人", "hook": "信件没有寄出", "recap": "检查北塔。",
        }, self.project)
        self.assertEqual(status, 201, result)
        self.second = result["id"]
        self.request("POST", "/api/chapters", {"title": "下一章草稿", "body": "", "status": "草稿"}, self.project)

    def stop_sentinels(self):
        for mock in self.mocks:
            mock.assert_not_called()
        for sentinel in reversed(self.sentinels):
            sentinel.stop()

    def request(self, method, path, data=None, project=None, branch=None, headers=None, binary=False):
        request_headers = {"Content-Type": "application/json"}
        if project:
            request_headers["X-Moyu-Project"] = project
        if branch:
            request_headers["X-Moyu-Branch"] = branch
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.base + path, data=json_bytes(data) if data is not None else None,
            headers=request_headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                return response.status, raw if binary else json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw)

    def snapshot(self):
        with server.connect() as conn:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ) if not row[0].startswith(("sqlite_", "app_search_"))]
            return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                    for table in tables}

    def count(self, table, project=None):
        with server.connect() as conn:
            return conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE project_id=?',
                                (project or self.project,)).fetchone()[0]

    def export(self, fmt):
        status, raw = self.request(
            "GET", "/api/export/" + fmt, project=self.project, binary=True,
            headers={"X-Moyu-Export-Override": "Author confirmed the complete manuscript"},
        )
        self.assertEqual(status, 200, raw)
        return raw

    def preview(self, query="请续写当前章", chapter=None, project=None, branch=None):
        status, result = self.request("POST", "/api/assistant/query", {
            "query": query, "chapter_id": chapter or self.chapter,
        }, project or self.project, branch)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["read_only"])
        return result

    def confirm(self, token, project=None, branch=None, **extra):
        return self.request("POST", "/api/assistant/confirm", {
            "token": token, "confirm": True, "defer": True, **extra,
        }, project or self.project, branch)

    def assert_confirmation_rejected(self, token, project=None, branch=None, **extra):
        before = self.snapshot()
        status, result = self.confirm(token, project, branch, **extra)
        self.assertIn(status, {400, 404, 409}, result)
        self.assertEqual(self.snapshot(), before)
        return result

    def test_publication_roundtrip_and_actual_import(self):
        expected = [{"title": "同名章节", "body": BODY_A}, {"title": "同名章节", "body": BODY_B}]
        for fmt in ("epub", "markdown"):
            with self.subTest(format=fmt):
                raw = self.export(fmt)
                filename = "作品.epub" if fmt == "epub" else "作品.md"
                parsed = moyu_import.parse_import(raw, filename)
                self.assertEqual([{key: chapter[key] for key in ("title", "body")}
                                  for chapter in parsed["chapters"]], expected)
                self.assertEqual(len({chapter["id"] for chapter in parsed["chapters"]}), 2)
                self.assertTrue(parsed["metadata"]["exact_decoded_text"])
                self.assertTrue(parsed["metadata"]["manuscript_text_roundtrip"])
                self.assertFalse(parsed["metadata"]["format_roundtrip"])
                self.assertFalse(any(chapter["heading_in_body"] for chapter in parsed["chapters"]))
                if fmt == "epub":
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        source = archive.read(moyu_publication.SOURCE_PATH)
                        manifest = json.loads(archive.read("META-INF/moyu-export-manifest.json"))
                        self.assertEqual(hashlib.sha256(source).hexdigest(), manifest["manuscript_source"]["sha256"])
                        self.assertEqual(json.loads(source)["chapters"], expected)
                        self.assertEqual(manifest["chapter_count"], 2)
                        for number, body in enumerate((BODY_A, BODY_B), 1):
                            document = ET.fromstring(archive.read(f"OEBPS/chapter-{number}.xhtml"))
                            pre = document.find("{http://www.w3.org/1999/xhtml}body/{http://www.w3.org/1999/xhtml}pre")
                            self.assertIsNotNone(pre)
                            self.assertEqual(pre.get("{http://www.w3.org/XML/1998/namespace}space"), "preserve")
                            self.assertEqual(pre.text, body.replace("\r\n", "\n").replace("\r", "\n"))
                            self.assertIn("white-space:pre-wrap", archive.read(f"OEBPS/chapter-{number}.xhtml").decode())
                status, preview = self.request("POST", "/api/import/preview", {
                    "filename": filename, "data_base64": base64.b64encode(raw).decode("ascii"),
                }, self.project)
                self.assertEqual(status, 200, preview)
                status, imported = self.request("POST", "/api/import/apply", {
                    "preview_id": preview["preview_id"], "source_sha256": preview["source_sha256"],
                    "mode": "manuscript", "title": "独立回读作品",
                }, self.project)
                self.assertEqual(status, 201, imported)
                self.assertNotEqual(imported["project"]["id"], self.project)
                self.assertEqual([{key: chapter[key] for key in ("title", "body")}
                                  for chapter in imported["state"]["chapters"]], expected)
                with server.connect() as conn:
                    stored = conn.execute("SELECT source_bytes FROM app_manuscript_import WHERE project_id=?",
                                          (imported["project"]["id"],)).fetchone()[0]
                    self.assertEqual(bytes(stored), raw)

    def test_source_bounds_types_empty_chapters_and_marker_damage(self):
        payload = {"format": "moyu-manuscript-v1", "version": 1, "project": {"title": "空章仍保留"},
                   "chapters": [{"title": "同名", "body": ""}, {"title": "同名", "body": ""},
                                {"title": "同名", "body": BODY_A}]}
        parsed = moyu_import.parse_import(source_marker(payload), "原文.md")
        self.assertEqual([chapter["body"] for chapter in parsed["chapters"]], ["", "", BODY_A])
        self.assertEqual(len({chapter["id"] for chapter in parsed["chapters"]}), 3)
        broken = [source_marker(payload, "0" * 64), source_marker(payload)[:-5],
                  source_marker(payload) + b"\n" + source_marker(payload),
                  b"<!-- moyu-manuscript-v1 broken -->", source_marker([])]
        mutations = [
            {"chapters": []}, {"chapters": [{}]}, {"chapters": [{"title": 1, "body": "x"}]},
            {"chapters": [{"title": "", "body": "x"}]}, {"chapters": [{"title": "x" * 501, "body": ""}]},
            {"chapters": [{"title": "x", "body": 1}]},
            {"chapters": [{"title": "x", "body": "x" * (moyu_import.MAX_CHAPTER_CHARS + 1)}]},
            {"chapters": [{"title": "x", "body": ""}] * (moyu_import.MAX_CHAPTERS + 1)},
            {"project": []}, {"version": 99}, {"version": True},
        ]
        broken.extend(source_marker({**payload, **mutation}) for mutation in mutations)
        before = self.snapshot()
        for raw in broken:
            with self.subTest(length=len(raw)):
                with self.assertRaises(moyu_import.ImportRejected):
                    moyu_import.parse_import(raw, "损坏.md")
                status, result = self.request("POST", "/api/import/preview", {
                    "filename": "损坏.md", "data_base64": base64.b64encode(raw).decode("ascii"),
                }, self.project)
                self.assertEqual(status, 400, result)
                self.assertEqual(self.snapshot(), before)

    def test_epub_damage_rejected_and_third_party_losses_remain_explicit(self):
        raw = self.export("epub")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            manifest = json.loads(archive.read("META-INF/moyu-export-manifest.json"))
            original_source = archive.read(moyu_publication.SOURCE_PATH)
        broken = [
            rewrite_epub(raw, {moyu_publication.SOURCE_PATH: original_source + b" "}),
            rewrite_epub(raw, removed=(moyu_publication.SOURCE_PATH,)),
            rewrite_epub(raw, removed=("META-INF/moyu-export-manifest.json",)),
            rewrite_epub(raw, {"OEBPS/chapter-1.xhtml": b"<broken"}),
            rewrite_epub(raw, {"../outside": b"bad"}),
        ]
        for mutation in (
            {"chapter_count": 3},
            {"chapter_count": True},
            {"manuscript_source": {"path": "../outside", "sha256": manifest["manuscript_source"]["sha256"]}},
            {"manuscript_source": {"path": moyu_publication.SOURCE_PATH, "sha256": "0" * 64}},
            {"chapters": []},
            {"chapters": [{**manifest["chapters"][0], "body_codepoints": 1}, manifest["chapters"][1]]},
            {"chapters": [{**manifest["chapters"][0], "body_sha256": "0" * 64}, manifest["chapters"][1]]},
        ):
            broken.append(rewrite_epub(raw, {
                "META-INF/moyu-export-manifest.json": json_bytes({**manifest, **mutation}),
            }))
        for value in ([], {"format": "moyu-manuscript-v1", "version": 1, "project": {},
                           "chapters": [{"title": "x", "body": 3}]}):
            source = json_bytes(value)
            broken.append(rewrite_epub(raw, {
                moyu_publication.SOURCE_PATH: source,
                "META-INF/moyu-export-manifest.json": json_bytes({
                    **manifest, "manuscript_source": {
                        "path": moyu_publication.SOURCE_PATH, "sha256": hashlib.sha256(source).hexdigest(),
                    },
                }),
            }))
        before = self.snapshot()
        for damaged in broken:
            with self.subTest(bytes=len(damaged)):
                with self.assertRaises(moyu_import.ImportRejected):
                    moyu_import.parse_import(damaged, "损坏.epub")
                status, result = self.request("POST", "/api/import/preview", {
                    "filename": "损坏.epub", "data_base64": base64.b64encode(damaged).decode("ascii"),
                }, self.project)
                self.assertEqual(status, 400, result)
                self.assertEqual(self.snapshot(), before)
        outside = moyu_import.parse_import(third_party_epub(), "外部.epub")
        self.assertEqual(outside["metadata"]["extraction"], "visible_xhtml_text")
        self.assertFalse(outside["metadata"]["format_roundtrip"])
        self.assertFalse(outside["metadata"]["exact_decoded_text"])
        body = outside["chapters"][0]["body"]
        self.assertIn("外部正文的可读文字", body)
        self.assertIn("港口的旧塔", body)
        self.assertNotIn("不得导入脚本正文", body)
        self.assertTrue(any("版式、样式、字体、图片和交互" in warning for warning in outside["warnings"]))

    def test_publication_gate_review_and_stale_review(self):
        for fmt in ("epub", "markdown"):
            status, result = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True)
            self.assertEqual(status, 409, result)
            status, result = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True,
                                          headers={"X-Moyu-Export-Override": "1234567"})
            self.assertEqual(status, 409, result)
        override = self.export("epub")
        with zipfile.ZipFile(io.BytesIO(override)) as archive:
            manifest = json.loads(archive.read("META-INF/moyu-export-manifest.json"))
            self.assertTrue(manifest["finalization"]["overridden"])
            self.assertEqual(manifest["author_override"]["reason"], "Author confirmed the complete manuscript")
        events = self.request("GET", "/api/events", project=self.project)[1]["events"]
        exported = next(event for event in events if event["event_type"] == "publication.exported")
        self.assertEqual(exported["payload_json"]["author_override"]["reason"], "Author confirmed the complete manuscript")
        for chapter in (self.chapter, self.second):
            status, result = self.request("POST", "/api/review", {"chapter_id": chapter}, self.project)
            self.assertEqual(status, 200, result)
            self.assertEqual(result["status"], "passed")
        for fmt in ("epub", "markdown"):
            status, raw = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True)
            self.assertEqual(status, 200, raw)
        status, changed = self.request("PATCH", "/api/chapters/" + self.chapter, {"body": BODY_A + "\n改稿"}, self.project)
        self.assertEqual(status, 200, changed)
        self.assertEqual(changed["review_status"], "unreviewed")
        for fmt in ("epub", "markdown"):
            status, result = self.request("GET", "/api/export/" + fmt, project=self.project, binary=True)
            self.assertEqual(status, 409, result)

    def test_payoff_gate_requires_current_evidence_and_reanchor_preserves_link(self):
        status, plot = self.request("POST", "/api/plot", {
            "text": "旧信中的铜环", "stage": "payoff",
        }, self.project)
        self.assertEqual(status, 201, plot)
        status, manifest = self.request("GET", "/api/export/manifest", project=self.project)
        self.assertEqual(status, 200)
        self.assertIn("伏笔“旧信中的铜环”还没有可核验的回收原句。", manifest["finalization"]["issues"])
        quote = BODY_A.splitlines()[0]
        evidence = {"plot_id": plot["id"], "chapter_id": self.chapter, "kind": "payoff",
                    "source_quote": quote, "source_start": 0, "source_end": len(quote)}
        status, link = self.request("POST", "/api/story/foreshadow-links", evidence, self.project)
        self.assertEqual(status, 201, link)
        old_span = link["source_span_id"]
        self.assertFalse(link["stale"])
        for chapter in (self.chapter, self.second):
            self.assertEqual(self.request("POST", "/api/review", {"chapter_id": chapter}, self.project)[0], 200)
        self.assertTrue(self.request("GET", "/api/export/manifest", project=self.project)[1]["finalization"]["allowed"])
        for body in (BODY_A.replace(quote, ""), BODY_A):
            self.assertEqual(self.request("PATCH", "/api/chapters/" + self.chapter, {"body": body}, self.project)[0], 200)
        stale = self.request("GET", "/api/story/foreshadow-links", project=self.project)[1]["foreshadowing"][0]
        self.assertTrue(stale["stale"])
        for chapter in (self.chapter, self.second):
            self.request("POST", "/api/review", {"chapter_id": chapter}, self.project)
        manifest = self.request("GET", "/api/export/manifest", project=self.project)[1]
        self.assertEqual(manifest["finalization"]["issues"], ["伏笔“旧信中的铜环”的回收原句需要重新确认。"])
        self.assertEqual(self.request("GET", "/api/export/markdown", project=self.project, binary=True)[0], 409)
        self.assertEqual(self.request("PATCH", "/api/story/foreshadow-links/" + link["id"],
                                      {"note": "正文已恢复"}, self.project)[0], 400)
        status, repaired = self.request("PATCH", "/api/story/foreshadow-links/" + link["id"],
                                        {**evidence, "note": "作者在当前稿重新确认"}, self.project)
        self.assertEqual(status, 200, repaired)
        self.assertEqual(repaired["id"], link["id"])
        self.assertNotEqual(repaired["source_span_id"], old_span)
        self.assertFalse(repaired["stale"])
        links = self.request("GET", "/api/story/foreshadow-links", project=self.project)[1]["foreshadowing"]
        self.assertEqual(len(links), 1)
        for chapter in (self.chapter, self.second):
            self.request("POST", "/api/review", {"chapter_id": chapter}, self.project)
        self.assertTrue(self.request("GET", "/api/export/manifest", project=self.project)[1]["finalization"]["allowed"])
        self.assertEqual(self.request("GET", "/api/export/markdown", project=self.project, binary=True)[0], 200)

    def test_assistant_query_is_read_only_for_every_tool(self):
        with patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "http://127.0.0.1:9", "MOYU_AI_API_KEY": "fixture-unused",
            "MOYU_AI_MODEL": "fixture-model", "MOYU_AI_DAILY_QUOTA": "1",
        }):
            before = self.snapshot()
            answer = self.preview("旧信在哪里")
            self.assertIsNone(answer["proposal"])
            tasks = {
                "continue": "请续写当前章", "polish": "请润色当前章", "summary": "请整理章节回顾",
                "chapter_analysis": "请提取章后记忆", "semantic_review": "请检查故事矛盾",
                "outline": "请规划全书大纲", "scene": "请规划本章场景",
            }
            for task, query in tasks.items():
                with self.subTest(task=task):
                    result = self.preview(query)
                    self.assertEqual(result["proposal"]["task"], task)
                    self.assertEqual(result["scope"], {"project_id": self.project, "branch_id": self.branch})
                    self.assertEqual(result["proposal"]["chapter_id"], self.chapter)
                    self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.count("app_ai_job"), 0)
            self.assertEqual(self.count("ai_run"), 0)
            self.assertEqual(self.count("app_quota_reservation"), 0)

    def test_assistant_token_confirmation_and_scope_guards(self):
        token = self.preview()["proposal"]["token"]
        self.assert_confirmation_rejected(token, confirm=False)
        self.assert_confirmation_rejected(token[:-1] + ("0" if token[-1] != "0" else "1"))
        status, other = self.request("POST", "/api/projects", {"title": "同名章节的另一本书"})
        self.assertEqual(status, 201, other)
        other_project = other["project"]["id"]
        self.request("GET", "/api/state", project=other_project)
        self.assert_confirmation_rejected(token, other_project)
        with server.connect() as conn:
            alternate = server.uid("branch_")
            conn.execute("INSERT INTO branch VALUES(?,?,?,?,?,?,?)",
                         (alternate, self.project, self.branch, None, None, "active", "{}"))
            conn.commit()
        self.request("GET", "/api/state", project=self.project, branch=alternate)
        self.assert_confirmation_rejected(token, self.project, alternate)
        token = self.preview()["proposal"]["token"]
        status, result = self.request("PATCH", "/api/chapters/" + self.chapter, {"title": "预览后改名"}, self.project)
        self.assertEqual(status, 200, result)
        self.assert_confirmation_rejected(token)
        token = self.preview()["proposal"]["token"]
        self.request("PATCH", "/api/chapters/" + self.chapter, {"body": BODY_A + "\n新段落"}, self.project)
        self.assert_confirmation_rejected(token)
        token = self.preview()["proposal"]["token"]
        self.request("POST", "/api/memory", {"name": "预览后新增记忆", "status": "待确认"}, self.project)
        self.assert_confirmation_rejected(token)
        token = self.preview()["proposal"]["token"]
        self.request("POST", "/api/story/timeline", {
            "title": "预览后新增事件", "chapter_id": self.chapter, "summary": "船在黎明到港",
        }, self.project)
        self.assert_confirmation_rejected(token)
        token = self.preview()["proposal"]["token"]
        self.request("PATCH", "/api/chapters/" + self.second, {"body": BODY_B + "\n另一章的新句"}, self.project)
        self.assert_confirmation_rejected(token)
        self.assertEqual(self.count("app_ai_job"), 0)
        self.assertEqual(self.count("app_assistant_request"), 0)

    def test_confirm_once_preserves_manuscript_and_restored_jobs_are_independent(self):
        before_state = self.request("GET", "/api/state", project=self.project)[1]
        before_structure = self.request("GET", "/api/story-map", project=self.project)[1]
        token = self.preview()["proposal"]["token"]
        with patch.dict(os.environ, {
            "MOYU_AI_BASE_URL": "http://127.0.0.1:9", "MOYU_AI_API_KEY": "fixture-unused",
            "MOYU_AI_MODEL": "fixture-model", "MOYU_AI_DAILY_QUOTA": "1",
        }):
            status, confirmed = self.confirm(token)
            self.assertEqual(status, 202, confirmed)
            job = confirmed["job"]["id"]
            self.assertFalse(confirmed["idempotent"])
            status, repeated = self.confirm(token)
            self.assertEqual(status, 200, repeated)
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(repeated["job"]["id"], job)
            self.assertEqual(self.count("app_ai_job"), 1)
            self.assertEqual(self.count("ai_run"), 1)
            self.assertEqual(self.count("app_assistant_request"), 1)
            self.assertEqual(self.count("app_quota_reservation"), 1)
        after_state = self.request("GET", "/api/state", project=self.project)[1]
        for key in ("project", "chapters", "records", "memory", "plot"):
            self.assertEqual(before_state[key], after_state[key])
        self.assertEqual(before_structure, self.request("GET", "/api/story-map", project=self.project)[1])
        status, backup = self.request("POST", "/api/backups", {}, self.project)
        self.assertEqual(status, 201, backup)
        recovered_ids, recovered_tokens, recovered_jobs = set(), set(), set()
        original_token_hash = server.hash_text(token)
        for _ in range(2):
            status, restored = self.request("POST", f"/api/backups/{backup['id']}/restore",
                                            {"confirm": True}, self.project)
            self.assertEqual(status, 201, restored)
            pid, bid, mapping = restored["project_id"], restored["branch_id"], restored["identity_map"]
            self.assertNotEqual(pid, self.project)
            self.assertNotEqual(bid, self.branch)
            recovered_ids.add(pid)
            recovered_tokens.add(mapping[original_token_hash])
            recovered_jobs.add(mapping[job])
            self.request("GET", "/api/state", project=pid)
            with server.connect() as conn:
                request = conn.execute("SELECT * FROM app_assistant_request WHERE project_id=?", (pid,)).fetchone()
                self.assertIsNotNone(request)
                self.assertEqual(request["token_hash"], mapping[original_token_hash])
                self.assertEqual(request["job_id"], mapping[job])
                self.assertEqual(request["branch_id"], bid)
                restored_job = conn.execute("SELECT * FROM app_ai_job WHERE id=?", (mapping[job],)).fetchone()
                self.assertEqual(restored_job["project_id"], pid)
                self.assertEqual(restored_job["branch_id"], bid)
                self.assertEqual(restored_job["status"], "failed")
                self.assertEqual(restored_job["cancel_requested"], 1)
                restored_run = conn.execute("SELECT * FROM ai_run WHERE id=?", (restored_job["ai_run_id"],)).fetchone()
                self.assertEqual(restored_run["status"], "unknown")
            self.assert_confirmation_rejected(token, pid, bid)
        self.assertEqual(len(recovered_ids), 2)
        self.assertEqual(len(recovered_tokens), 2)
        self.assertEqual(len(recovered_jobs), 2)
        self.assertNotIn(original_token_hash, recovered_tokens)
        self.assertNotIn(job, recovered_jobs)
        with server.connect() as conn:
            original = conn.execute("SELECT * FROM app_assistant_request WHERE project_id=?", (self.project,)).fetchone()
            self.assertEqual(original["token_hash"], original_token_hash)
            self.assertEqual(original["job_id"], job)
            self.assertEqual(conn.execute("SELECT status FROM app_ai_job WHERE id=?", (job,)).fetchone()[0], "queued")
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        status, repeated = self.confirm(token)
        self.assertEqual(status, 200, repeated)
        self.assertEqual(repeated["job"]["id"], job)
        self.assertTrue(repeated["idempotent"])

    def test_backup_restore_and_duplicate_require_independent_credentials(self):
        preferences = {
            "base_url": "http://127.0.0.1:9", "model": "fixture-model",
            "models": {"continue": "fixture-prose"}, "api_key": "fixture-private-original",
            "styles": {"tone": "冷静", "instructions": "保留人物的说话习惯"},
            "parameters": {"temperature": 0.6, "max_tokens": 3000}, "daily_limit": 2,
        }
        status, saved = self.request("PUT", "/api/ai/settings", preferences, self.project)
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved["settings"]["api_key_configured"])
        with server.connect() as conn:
            original_settings = json.loads(conn.execute(
                "SELECT settings_json FROM app_model_settings WHERE project_id=?", (self.project,)
            ).fetchone()[0])
            original_ref = original_settings["_credential_ref"]
            self.assertTrue(original_ref)
            captured = moyu_backups.capture(conn, self.project)
            portable = json.loads(captured["tables"]["app_model_settings"][0]["settings_json"])
            self.assertIsNone(portable["_credential_ref"])
            self.assertIsNone(portable["last_probe"])
        original_path = self.root / "private-keys" / (original_ref + ".json")
        self.assertTrue(original_path.is_file())
        original_key_bytes = original_path.read_bytes()
        status, backup = self.request("POST", "/api/backups", {}, self.project)
        self.assertEqual(status, 201, backup)
        package = self.root / "backups" / (backup["id"] + ".zip")
        with zipfile.ZipFile(package) as archive:
            payload = json.loads(archive.read("project.json"))
        portable = json.loads(payload["tables"]["app_model_settings"][0]["settings_json"])
        self.assertIsNone(portable["_credential_ref"])
        self.assertNotIn(preferences["api_key"], json.dumps(payload))
        status, restored = self.request("POST", f"/api/backups/{backup['id']}/restore",
                                        {"confirm": True}, self.project)
        self.assertEqual(status, 201, restored)
        status, duplicate = self.request("POST", f"/api/projects/{self.project}/duplicate",
                                         {"title": "凭据独立副本"}, self.project)
        self.assertEqual(status, 201, duplicate)
        new_projects = [restored["project"]["id"], duplicate["project"]["id"]]
        # A legacy archive with an original reference must be sanitized too.
        with server.connect() as conn:
            legacy = copy.deepcopy(captured)
            legacy["tables"]["app_model_settings"][0]["settings_json"] = json.dumps(original_settings)
            legacy_project = server.uid("book_")
            moyu_backups.transplant(conn, legacy, legacy_project)
            conn.commit()
            new_projects.append(legacy_project)
        for project in new_projects:
            with self.subTest(project=project):
                status, response = self.request("GET", "/api/ai/settings", project=project)
                self.assertEqual(status, 200, response)
                public = response["settings"]
                self.assertTrue(public["configured"])
                self.assertFalse(public["api_key_configured"])
                for key in ("base_url", "model", "models", "styles", "parameters", "daily_limit"):
                    self.assertEqual(public[key], preferences[key])
                self.assertIsNone(public["last_probe"])
                with server.connect() as conn:
                    internal = json.loads(conn.execute(
                        "SELECT settings_json FROM app_model_settings WHERE project_id=?", (project,)
                    ).fetchone()[0])
                    self.assertIsNone(internal["_credential_ref"])
                    self.assertEqual(moyu_settings.provider_settings(conn, project)["api_key"], "")
        status, saved = self.request("PUT", "/api/ai/settings", {
            "api_key": "fixture-private-restored",
        }, new_projects[0])
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved["settings"]["api_key_configured"])
        with server.connect() as conn:
            copied_ref = json.loads(conn.execute(
                "SELECT settings_json FROM app_model_settings WHERE project_id=?", (new_projects[0],)
            ).fetchone()[0])["_credential_ref"]
            self.assertNotEqual(copied_ref, original_ref)
            self.assertEqual(moyu_settings.provider_settings(conn, self.project)["api_key"], preferences["api_key"])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(original_path.read_bytes(), original_key_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
