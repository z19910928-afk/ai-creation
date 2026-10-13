#!/usr/bin/env python3
"""Exact manuscript import, malicious EPUB rejection and reference isolation."""
from __future__ import annotations

import copy
import hashlib
import io
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moyu_import import (  # noqa: E402
    ImportRejected, ensure_reference_tables, list_import_sources, list_references,
    parse_import, reference_details, save_import_source, save_reference,
    search_references, validate_import_selection,
)
from moyu_context import text_hash  # noqa: E402


def epub_bytes(*, first_body="林岚按住左肩。\n", second_body="第二页正文。", extra=None,
               container=None, opf=None, compression=zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    default_container = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/>'
        '</rootfiles></container>'
    )
    default_opf = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>夜航旧书</dc:title></metadata>'
        '<manifest>'
        '<item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '</manifest><spine><itemref idref="two"/><itemref idref="one" linear="no"/></spine></package>'
    )
    def xhtml(title, body):
        return f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>'
    files = {
        "META-INF/container.xml": container or default_container,
        "OEBPS/book.opf": opf or default_opf,
        "OEBPS/one.xhtml": xhtml("同名章", first_body),
        "OEBPS/two.xhtml": xhtml("同名章", second_body),
        "OEBPS/notes.xhtml": xhtml("脚注", "守灯人的脚注也要保留。"),
        "OEBPS/nav.xhtml": xhtml("目录", '<a href="one.xhtml">导航文字</a>'),
        **dict(extra or {}),
    }
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for filename, data in files.items():
            archive.writestr(filename, data, compress_type=compression)
    return output.getvalue()


class ImportParsingTests(unittest.TestCase):
    def assert_exact(self, raw: bytes, filename: str, text: str):
        parsed = parse_import(raw, filename)
        self.assertEqual("".join(item["body"] for item in parsed["chapters"]), text)
        self.assertEqual(parsed["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(parsed["metadata"]["text_sha256"], text_hash(text))
        self.assertEqual(parsed["metadata"]["characters"], len(text))
        for item in parsed["chapters"]:
            self.assertEqual(text[item["source_start"]:item["source_end"]], item["body"])
            self.assertEqual(item["source_hash"], text_hash(item["body"]))
        return parsed

    def test_utf8_short_preface_duplicate_and_empty_chapter_are_retained(self):
        text = "很短的序。\r\n\r\n第一章 夜潮\r\n林岚看向北塔。\r\n\r\n第一章 夜潮\r\n\r\n第二章 归来\r\n守灯人回来了。\r\n"
        parsed = self.assert_exact(text.encode(), "old.txt", text)
        self.assertEqual([item["title"] for item in parsed["chapters"]], ["导入前言", "第一章 夜潮", "第一章 夜潮", "第二章 归来"])
        self.assertTrue(parsed["chapters"][2]["empty_content"])
        self.assertFalse(parsed["chapters"][0]["empty_content"])
        self.assertEqual(len({item["id"] for item in parsed["chapters"]}), 4)
        self.assertTrue(any("同名" in item for item in parsed["warnings"]))

    def test_gb18030_and_utf8_bom_preserve_every_codepoint(self):
        text = "序言\n寒潮到港。\n第一章 铜匙\n林岚拿出红铜罗盘。\n"
        parsed = self.assert_exact(text.encode("gb18030"), "中文.TXT", text)
        self.assertEqual(parsed["metadata"]["encoding"], "gb18030")
        bom_text = "\ufeff" + text
        parsed = self.assert_exact(bom_text.encode(), "withbom.txt", bom_text)
        self.assertEqual(parsed["chapters"][0]["body"][0], "\ufeff")
        self.assertTrue(parsed["metadata"]["exact_decoded_text"])

    def test_no_heading_is_one_exact_chapter(self):
        text = "没有标题。\n\n一段原文里有　全角空格与\t制表符。\n末尾保留空行。\n\n"
        parsed = self.assert_exact(text.encode(), "无标题.txt", text)
        self.assertEqual(len(parsed["chapters"]), 1)
        self.assertEqual(parsed["chapters"][0]["title"], "无标题")

    def test_markdown_title_preface_and_fenced_pseudo_headings(self):
        text = "# 夜航\n短序言。\n\n## 第一章 夜潮\n原文。\n```md\n## 假章\n第一章 代码里的标题\n```\n\n## 第一章 夜潮\n\n## 第二章 铜匙\n𠀀字和🙂都保留。\n"
        parsed = self.assert_exact(text.encode(), "novel.md", text)
        self.assertEqual([item["title"] for item in parsed["chapters"]], ["导入前言", "第一章 夜潮", "第一章 夜潮", "第二章 铜匙"])
        self.assertIn("## 假章", parsed["chapters"][1]["body"])
        self.assertTrue(parsed["chapters"][2]["empty_content"])
        self.assertTrue(parsed["chapters"][1]["heading_in_body"])

    def test_split_and_merge_preview_only_changes_boundaries_and_titles(self):
        text = "第一章 夜潮\n林岚进门。\n守灯人离开。\n第二章 北塔\n罗盘仍在。\n"
        parsed = self.assert_exact(text.encode(), "novel.txt", text)
        midpoint = text.index("守灯人")
        selected = validate_import_selection(parsed, {
            "source_sha256": parsed["source_sha256"], "mode": "manuscript", "title": "我的旧书",
            "chapters": [
                {"title": "前半", "body": text[:midpoint]},
                {"title": "后半", "body": text[midpoint:]},
                {"title": "暂留空章", "body": ""},
            ],
        })
        self.assertEqual("".join(item["body"] for item in selected["chapters"]), text)
        self.assertEqual(selected["chapters"][2]["body"], "")
        for broken in (
            [{"title": "缺字", "body": text[:-1]}],
            [{"title": "改字", "body": text.replace("罗盘", "短剑")}],
            [{"title": "重排", "body": text[midpoint:] + text[:midpoint]}],
            [{"title": "重复", "body": text + text}],
        ):
            with self.assertRaisesRegex(ImportRejected, "原文"):
                validate_import_selection(parsed, {"source_sha256": parsed["source_sha256"], "chapters": broken})
        with self.assertRaisesRegex(ImportRejected, "改变"):
            validate_import_selection(parsed, {"source_sha256": "0" * 64})

    def test_extreme_single_chapter_splits_without_loss(self):
        text = "第一章 一整章\n" + "长句原文；" * 150
        with patch("moyu_import.MAX_CHAPTER_CHARS", 300):
            parsed = self.assert_exact(text.encode(), "long.txt", text)
        self.assertGreater(len(parsed["chapters"]), 1)
        self.assertTrue(all(len(item["body"]) <= 300 for item in parsed["chapters"]))

    def test_epub_uses_spine_order_and_preserves_notes_and_duplicate_titles(self):
        raw = epub_bytes(first_body="林岚的左肩旧伤仍疼。", second_body="北塔先行的正文。")
        parsed = parse_import(raw, "旧书.epub")
        self.assertEqual(parsed["metadata"]["suggested_title"], "夜航旧书")
        self.assertIn("北塔先行的正文。", parsed["chapters"][0]["body"])
        self.assertIn("林岚的左肩旧伤仍疼。", parsed["chapters"][1]["body"])
        self.assertIn("守灯人的脚注也要保留。", parsed["chapters"][2]["body"])
        self.assertNotIn("导航文字", "".join(item["body"] for item in parsed["chapters"]))
        self.assertEqual(parsed["chapters"][0]["title"], parsed["chapters"][1]["title"])
        self.assertFalse(parsed["metadata"]["exact_decoded_text"])
        self.assertFalse(parsed["metadata"]["format_roundtrip"])
        self.assertTrue(parsed["chapters"][2]["supplemental"])
        self.assertTrue(any("版式" in warning for warning in parsed["warnings"]))
        for chapter in parsed["chapters"]:
            self.assertEqual(len(chapter["source_member_hash"]), 64)

    def test_epub_inline_text_image_alt_and_empty_spine_item(self):
        raw = epub_bytes(first_body="林<span>岚</span>握紧<strong>红铜罗盘</strong>。<br/>尾句。<img alt=\"灯塔地图\" src=\"map.png\"/>", second_body="")
        parsed = parse_import(raw, "novel.epub")
        body = parsed["chapters"][1]["body"]
        self.assertIn("林岚握紧红铜罗盘。", body)
        self.assertIn("尾句。", body)
        self.assertIn("图片说明：灯塔地图", body)
        self.assertEqual(len(parsed["chapters"]), 3)

    def test_epub_two_public_doctype_is_local_and_entity_subset_is_rejected(self):
        chapter = (
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>旧书章节</h1><p>旧格式正文。</p></body></html>'
        )
        parsed = parse_import(epub_bytes(extra={"OEBPS/one.xhtml": chapter}), "epub2.epub")
        self.assertIn("旧格式正文。", parsed["chapters"][1]["body"])
        for malicious in (
            '<!DOCTYPE html [<!ENTITY a "原文">]><html><body>&a;</body></html>',
            '<!DOCTYPE html [<!ENTITY % outside SYSTEM "http://example.invalid/ext">%outside;]><html><body>正文</body></html>',
            "<html><body>" + "<div>" * 150 + "nested" + "</div>" * 150 + "</body></html>",
        ):
            with self.assertRaises(ImportRejected):
                parse_import(epub_bytes(extra={"OEBPS/one.xhtml": malicious}), "rejected.epub")

    def test_damaged_binary_empty_and_unsupported_files_are_rejected(self):
        for raw, filename in ((b"bad zip", "bad.epub"), (b"\x00binary", "binary.txt"), (b"", "empty.txt"), (b"  \n", "space.md"), (b"hello", "book.pdf")):
            with self.subTest(filename=filename), self.assertRaises(ImportRejected):
                parse_import(raw, filename)
        with patch("moyu_import.MAX_SOURCE_BYTES", 5):
            with self.assertRaises(ImportRejected):
                parse_import(b"toolarge", "big.txt")

    def test_epub_traversal_entities_missing_member_and_remote_body_rejected(self):
        malicious_opf = (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>'
            '<item id="bad" href="https://example.invalid/body.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="bad"/></spine></package>'
        )
        samples = [
            epub_bytes(extra={"../../escape.txt": "outside"}),
            epub_bytes(extra={"OEBPS\\escape.txt": "outside"}),
            epub_bytes(extra={"OEBPS/one.xhtml": '<!DOCTYPE html [<!ENTITY ext SYSTEM "file:///etc/passwd">]><html><body>&ext;</body></html>'}),
            epub_bytes(extra={"OEBPS/one.xhtml": "<html><body>broken"}),
            epub_bytes(opf=malicious_opf),
            epub_bytes(container='<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="../bad.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'),
        ]
        for number, raw in enumerate(samples):
            with self.subTest(number=number), self.assertRaises(ImportRejected):
                parse_import(raw, "malicious.epub")
        with patch("moyu_import.MAX_EPUB_MEMBER_BYTES", 100):
            with self.assertRaises(ImportRejected):
                parse_import(epub_bytes(), "large.epub")
        with patch("moyu_import.MAX_EPUB_RATIO", 2):
            with self.assertRaises(ImportRejected):
                parse_import(epub_bytes(extra={"OEBPS/bomb": "a" * 1_100_000}), "bomb.epub")
        with patch("moyu_import.MAX_EPUB_EXPANDED_BYTES", 50):
            with self.assertRaises(ImportRejected):
                parse_import(epub_bytes(), "expanded.epub")

    def test_epub_duplicate_zip_members_and_crc_damage_are_rejected(self):
        raw = epub_bytes(compression=zipfile.ZIP_STORED)
        damaged = bytearray(raw)
        offset = damaged.index(b"application/epub+zip")
        damaged[offset] ^= 1
        with self.assertRaises(ImportRejected):
            parse_import(bytes(damaged), "crc.epub")
        output = io.BytesIO(raw)
        with zipfile.ZipFile(output, "a") as archive:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                archive.writestr("OEBPS/one.xhtml", "duplicate")
        with self.assertRaisesRegex(ImportRejected, "重复"):
            parse_import(output.getvalue(), "duplicate.epub")


class ReferenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE project(id TEXT PRIMARY KEY);
            CREATE TABLE branch(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES project(id));
            INSERT INTO project VALUES('book');
            INSERT INTO project VALUES('other');
            INSERT INTO branch VALUES('main','book');
            INSERT INTO branch VALUES('alternate','book');
            INSERT INTO branch VALUES('other-main','other');
        """)
        ensure_reference_tables(self.conn)
        self.conn.commit()
        self.scope = {"project_id": "book", "branch_id": "main"}
        self.text = "第一章 河岸\n林岚的红铜罗盘指向北塔。\n\n第二章 旧伤\n林岚左肩的伤来自木刺。\n"
        self.raw = self.text.encode()
        self.parsed = parse_import(self.raw, "reference.txt")
        self.selection = validate_import_selection(self.parsed, {
            "mode": "reference", "source_sha256": self.parsed["source_sha256"], "title": "资料旧书",
        })

    def tearDown(self):
        self.conn.close()

    def test_original_archive_is_exact_private_and_rollbackable(self):
        saved = save_import_source(self.conn, self.scope, self.parsed, self.raw)
        self.assertEqual(saved["metadata"]["encoding"], "utf-8")
        self.assertNotIn("source_bytes", saved)
        row = self.conn.execute("SELECT source_bytes,source_sha256 FROM app_manuscript_import").fetchone()
        self.assertEqual(row, (self.raw, hashlib.sha256(self.raw).hexdigest()))
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(list_import_sources(self.conn, self.scope), [])
        first = save_import_source(self.conn, self.scope, self.parsed, self.raw)
        second = save_import_source(self.conn, self.scope, self.parsed, self.raw)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(list_import_sources(self.conn, self.scope)), 1)

    def test_reference_exact_provenance_idempotency_and_search_scope(self):
        saved = save_reference(self.conn, self.scope, self.parsed, self.selection, self.raw)
        duplicate = save_reference(self.conn, self.scope, self.parsed, self.selection, self.raw)
        self.assertEqual(saved["id"], duplicate["id"])
        self.assertEqual(len(list_references(self.conn, self.scope)), 1)
        self.assertNotIn("source_bytes", saved)
        detail = reference_details(self.conn, self.scope, saved["id"])
        self.assertEqual("".join(item["body"] for item in detail["chapters"]), self.text)
        results = search_references(self.conn, self.scope, "红铜罗盘指向北塔", document_id=saved["id"])
        self.assertTrue(results["results"])
        for hit in results["results"]:
            self.assertEqual(hit["use"], "reference_not_story_fact")
            self.assertEqual(hit["material_kind"], "reference")
            self.assertEqual(self.text[hit["source_start"]:hit["source_end"]], hit["source_quote"])
            self.assertEqual(hit["source_hash"], text_hash(hit["source_quote"]))
            self.assertNotIn("source_chapter_id", hit)
        for foreign_scope in (
            {"project_id": "other", "branch_id": "other-main"},
            {"project_id": "book", "branch_id": "alternate"},
        ):
            self.assertEqual(list_references(self.conn, foreign_scope), [])
            self.assertEqual(search_references(self.conn, foreign_scope, "红铜罗盘")["results"], [])
            with self.assertRaises(ImportRejected):
                reference_details(self.conn, foreign_scope, saved["id"])
        self.assertEqual(search_references(self.conn, self.scope, "红铜罗盘", limit=0)["results"], [])

    def test_reference_write_caller_rollback_and_source_mismatch(self):
        save_reference(self.conn, self.scope, self.parsed, self.selection, self.raw)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(list_references(self.conn, self.scope), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_reference_section").fetchone()[0], 0)
        with self.assertRaisesRegex(ImportRejected, "校验"):
            save_reference(self.conn, self.scope, self.parsed, self.selection, b"another source")
        self.assertEqual(list_references(self.conn, self.scope), [])
        altered = copy.deepcopy(self.selection)
        altered["chapters"][0]["body"] += "新增文字"
        with self.assertRaisesRegex(ImportRejected, "原文"):
            save_reference(self.conn, self.scope, self.parsed, altered, self.raw)
        self.assertEqual(list_references(self.conn, self.scope), [])
        wrong_branch = {"project_id": "book", "branch_id": "other-main"}
        with self.assertRaisesRegex(ImportRejected, "不一致"):
            save_reference(self.conn, wrong_branch, self.parsed, self.selection, self.raw)
        with self.assertRaisesRegex(ImportRejected, "不一致"):
            save_import_source(self.conn, wrong_branch, self.parsed, self.raw)

    def test_reference_search_reports_a_bounded_complete_section_subset(self):
        save_reference(self.conn, self.scope, self.parsed, self.selection, self.raw)
        first_size = len(self.parsed["chapters"][0]["body"].encode("utf-8"))
        with patch("moyu_import.MAX_SEARCH_UTF8_BYTES", first_size):
            results = search_references(self.conn, self.scope, "红铜罗盘")
        self.assertTrue(results["limited"])
        self.assertEqual(results["sections_considered"], 1)
        self.assertEqual(results["total_sections"], 2)
        self.assertEqual(results["source_utf8_bytes_considered"], first_size)
        self.assertTrue(results["warnings"])
        self.assertTrue(results["results"])

    def test_epub_original_bytes_and_format_losses_survive_archive(self):
        raw = epub_bytes()
        parsed = parse_import(raw, "novel.epub")
        saved = save_import_source(self.conn, self.scope, parsed, raw)
        self.assertFalse(saved["metadata"]["format_roundtrip"])
        self.assertTrue(any("版式" in item for item in saved["metadata"]["warnings"]))
        self.assertEqual(self.conn.execute("SELECT source_bytes FROM app_manuscript_import").fetchone()[0], raw)


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
