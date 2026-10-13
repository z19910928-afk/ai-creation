"""Bounded manuscript import and separately scoped reference-library retrieval.

TXT/Markdown chapter bodies are exact slices of decoded source text, including
the original headings and whitespace. EPUB preserves the original file and
extracts visible spine text; its layout losses are reported explicitly.
Nothing in this module promotes reference prose into the manuscript's canon.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import hashlib
import io
import json
import posixpath
import re
import sqlite3
import stat
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit
import uuid
import xml.etree.ElementTree as ET
from xml.parsers import expat
import zipfile

from moyu_context import ScopePolicy, paragraph_ranges, search_materials, text_hash


MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_EPUB_MEMBER_BYTES = 16 * 1024 * 1024
MAX_EPUB_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_EPUB_ENTRIES = 4096
MAX_EPUB_RATIO = 200
MAX_CHAPTERS = 2000
MAX_CHAPTER_CHARS = 2_000_000
MAX_SEARCH_SECTIONS = 4000
MAX_SEARCH_UTF8_BYTES = 16 * 1024 * 1024
MAX_XML_DEPTH = 128
MAX_XML_ELEMENTS = 250_000
_OPF = "http://www.idpf.org/2007/opf"
_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
_CHAPTER_HEADING = re.compile(
    r"^(?:第[ \t]*[0-9０-９零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟]+"
    r"[ \t]*[章节回卷部](?:[ \t:：、.．].*|[^。！？!?]{0,100})?"
    r"|(?:序章|楔子|序言|前言|引子|尾声|后记|终章|番外)(?:[ \t:：、].*)?"
    r"|chapter[ \t]+[0-9]+(?:[ \t:：.-].*)?)$",
    re.IGNORECASE,
)
_BLOCKS = {
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "ol", "p", "pre", "section", "table",
    "tr", "ul",
}


class ImportRejected(ValueError):
    """A malformed or unbounded import; the caller must leave its book intact."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _filename(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 500 or any(ord(char) < 32 for char in value):
        raise ImportRejected("文件名为空、过长或包含非法字符。")
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _decode_text(raw: bytes) -> tuple[str, str, list[str]]:
    for encoding in ("utf-8", "gb18030"):
        try:
            text = raw.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            raise ImportRejected("文件含有二进制内容，不能作为文字导入。")
        warnings = []
        if encoding == "gb18030":
            warnings.append("文件按 GB18030 中文编码读取，请在预览中确认文字。")
        if text.startswith("\ufeff"):
            warnings.append("原文件的开头标记已保留，正文没有删字。")
        return text, encoding, warnings
    raise ImportRejected("无法读取文件文字，请使用 UTF-8 或 GB18030 编码的 TXT/Markdown。")


def _chapter_title(line: str, markdown: bool = False) -> str:
    title = line.strip(" \t\r\n\ufeff")
    if markdown:
        title = re.sub(r"^#{1,6}[ \t]+", "", title)
        title = re.sub(r"[ \t]+#+[ \t]*$", "", title)
    return title[:500] or "未命名章节"


def _split_text(text: str, filename: str, digest: str, markdown: bool) -> tuple[list[dict[str, Any]], list[str]]:
    headings: list[tuple[int, int, str]] = []
    natural: list[tuple[int, int, str]] = []
    cursor = 0
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        candidate = line.rstrip("\r\n").lstrip(" \t\ufeff")
        marker = re.match(r"^(`{3,}|~{3,})", candidate) if markdown else None
        if marker:
            sequence = marker.group(1)
            if fence is None:
                fence = (sequence[0], len(sequence))
            elif sequence[0] == fence[0] and len(sequence) >= fence[1]:
                fence = None
            cursor += len(line)
            continue
        if fence is None:
            matched = re.match(r"^(#{1,6})[ \t]+(.+)$", candidate) if markdown else None
            if matched:
                headings.append((cursor, len(matched.group(1)), _chapter_title(candidate, True)))
            elif len(candidate) <= 200 and _CHAPTER_HEADING.fullmatch(candidate):
                natural.append((cursor, 0, _chapter_title(candidate)))
        cursor += len(line)
    warnings = []
    if headings:
        levels = sorted({item[1] for item in headings})
        repeated = [level for level in levels if sum(item[1] == level for item in headings) > 1]
        chapter_levels = [depth for _, depth, title in headings if _CHAPTER_HEADING.fullmatch(title)]
        level = min(chapter_levels) if chapter_levels else (repeated[0] if repeated else levels[0])
        boundaries = [(start, title) for start, depth, title in headings if depth == level]
        warnings.append("Markdown 按章节标题分章；原标题行、空行和代码块均保留在正文中。")
    else:
        boundaries = [(start, title) for start, _, title in natural]
    if not boundaries:
        boundaries = [(0, posixpath.splitext(filename)[0] or "完整正文")]
        warnings.append("没有找到章节标题，暂按一章导入，可在预览中调整拆分。")
    elif boundaries[0][0] > 0:
        boundaries.insert(0, (0, "导入前言"))
    heading_starts = {start for start, _, _ in headings + natural}
    chapters = []
    for ordinal, (start, title) in enumerate(boundaries):
        end = boundaries[ordinal + 1][0] if ordinal + 1 < len(boundaries) else len(text)
        section_end = end
        part = 0
        while start < section_end or (start == section_end and part == 0):
            end = min(section_end, start + MAX_CHAPTER_CHARS)
            if end < section_end:
                newline = text.rfind("\n", start + MAX_CHAPTER_CHARS // 2, end)
                if newline != -1:
                    end = newline + 1
            body = text[start:end]
            part += 1
            item_title = title if part == 1 else f"{title[:480]}（续 {part}）"
            has_heading = start in heading_starts
            heading_only = has_heading and not body.partition("\n")[2].strip()
            chapters.append({
                "id": "import_" + text_hash(f"{digest}:{start}:{end}")[:24],
                "title": item_title, "body": body, "source_start": start, "source_end": end,
                "source_hash": text_hash(body), "characters": len(body),
                "non_whitespace_characters": len(re.sub(r"\s", "", body)),
                "empty_content": heading_only or not body.strip("\ufeff \t\r\n"),
                "heading_in_body": has_heading,
            })
            start = end
            if start == section_end:
                break
        if part > 1:
            warnings.append(f"超长章节“{title}”分为 {part} 部分，原文顺序保持不变。")
    if "".join(item["body"] for item in chapters) != text:
        raise ImportRejected("拆章校验失败，原文件未保存。")
    return chapters, warnings


def _zip_member_name(name: str) -> str:
    if not name or "\\" in name or name.startswith("/") or any(ord(char) < 32 for char in name):
        raise ImportRejected("EPUB 含有非法文件路径。")
    parts = name.rstrip("/").split("/")
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise ImportRejected("EPUB 含有越界文件路径。")
    return name


def _safe_archive(raw: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ImportRejected("EPUB 文件损坏，无法读取书籍目录。") from exc
    try:
        if not infos or len(infos) > MAX_EPUB_ENTRIES:
            raise ImportRejected("EPUB 的内部文件数量超出导入上限。")
        seen, total = set(), 0
        for info in infos:
            _zip_member_name(info.filename)
            if info.filename in seen:
                raise ImportRejected("EPUB 含有重复的内部文件名。")
            seen.add(info.filename)
            if info.flag_bits & 1:
                raise ImportRejected("EPUB 包含加密文件，无法读取。")
            if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                raise ImportRejected("EPUB 含有符号链接，不能导入。")
            if info.file_size > MAX_EPUB_MEMBER_BYTES:
                raise ImportRejected("EPUB 的单个内部文件超过 16 MB。")
            if info.file_size > 1024 * 1024 and info.file_size > max(1, info.compress_size) * MAX_EPUB_RATIO:
                raise ImportRejected("EPUB 的压缩膨胀比例异常，不能导入。")
            total += info.file_size
            if total > MAX_EPUB_EXPANDED_BYTES:
                raise ImportRejected("EPUB 解压后的内容超过 128 MB。")
        if "mimetype" not in seen or archive.read("mimetype") != b"application/epub+zip":
            raise ImportRejected("此文件不是有效 EPUB：缺少书籍类型标记。")
        if infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED:
            raise ImportRejected("EPUB 的书籍类型标记顺序或压缩方式不符合格式要求。")
    except BaseException:
        archive.close()
        raise
    return archive


def _read_member(archive: zipfile.ZipFile, path: str) -> bytes:
    try:
        info = archive.getinfo(path)
        if info.is_dir() or info.file_size > MAX_EPUB_MEMBER_BYTES:
            raise ImportRejected("EPUB 的正文文件无效或过大。")
        with archive.open(info) as stream:
            raw = stream.read(MAX_EPUB_MEMBER_BYTES + 1)
        if len(raw) != info.file_size or len(raw) > MAX_EPUB_MEMBER_BYTES:
            raise ImportRejected("EPUB 的正文文件长度校验失败。")
        return raw
    except (KeyError, zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise ImportRejected("EPUB 的正文缺失、损坏或采用了不支持的压缩方式。") from exc


def _xml(raw: bytes, label: str) -> ET.Element:
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16", errors="strict")
        else:
            text = raw.decode("utf-8-sig", errors="strict")
        if "\x00" in text:
            raise ImportRejected(f"EPUB 的{label}含有非法字符。")
        parser = expat.ParserCreate()
        depth, elements = 0, 0
        def reject_entity(*_args):
            raise ImportRejected(f"EPUB 的{label}含有实体定义或外部引用，不能导入。")
        def doctype(_name, _system, _public, internal):
            if internal:
                reject_entity()
        def start(_name, _attributes):
            nonlocal depth, elements
            depth += 1
            elements += 1
            if depth > MAX_XML_DEPTH or elements > MAX_XML_ELEMENTS:
                raise ImportRejected(f"EPUB 的{label}结构过于复杂，不能导入。")
        def end(_name):
            nonlocal depth
            depth -= 1
        parser.EntityDeclHandler = reject_entity
        parser.ExternalEntityRefHandler = reject_entity
        parser.StartDoctypeDeclHandler = doctype
        parser.StartElementHandler, parser.EndElementHandler = start, end
        # Harmless XHTML public doctypes occur in EPUB 2. Expat never resolves
        # them here; internal subsets and entity declarations are rejected.
        parser.Parse(text, True)
        return ET.fromstring(text)
    except (UnicodeDecodeError, ET.ParseError, expat.ExpatError, LookupError, ValueError) as exc:
        if isinstance(exc, ImportRejected):
            raise
        raise ImportRejected(f"EPUB 的{label}格式损坏。") from exc


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].casefold() if isinstance(element.tag, str) else ""


def _resolve_member(base: str, href: str) -> str:
    if not href or len(href) > 2000:
        raise ImportRejected("EPUB 正文引用缺少有效地址。")
    split = urlsplit(href)
    if split.scheme or split.netloc or split.query:
        raise ImportRejected("EPUB 引用了外部正文，本地导入不会访问外部网站。")
    path = unquote(split.path, encoding="utf-8", errors="strict")
    if not path or path.startswith("/") or "\\" in path or any(ord(char) < 32 for char in path):
        raise ImportRejected("EPUB 的正文路径无效。")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    _zip_member_name(resolved)
    return resolved


def _visible_text(body: ET.Element) -> str:
    pieces: list[str] = []
    def boundary():
        if pieces and not pieces[-1].endswith("\n"):
            pieces.append("\n")
    def visit(element: ET.Element):
        name = _local_name(element)
        if name in {"script", "style"}:
            return
        if name == "br":
            pieces.append("\n")
        elif name in _BLOCKS:
            boundary()
        if name == "img" and element.get("alt"):
            pieces.append("[图片说明：" + element.get("alt", "") + "]")
        if element.text:
            pieces.append(element.text)
        for child in element:
            visit(child)
            if child.tail:
                pieces.append(child.tail)
        if name in _BLOCKS:
            boundary()
        elif name in {"td", "th"}:
            pieces.append("\t")
    visit(body)
    return "".join(pieces)


def _exact_preview(raw: bytes, filename: str, fmt: str, encoding: str,
                   payload: Mapping[str, Any], source_digest: str, warnings: list[str],
                   extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Use the bounded publication payload, not the rendered chapter headings."""
    digest = _digest(raw)
    chapters, cursor = [], 0
    for ordinal, chapter in enumerate(payload["chapters"]):
        body = chapter["body"]
        chapters.append({
            "id": "import_" + text_hash(f"{digest}:{ordinal}:{cursor}:{len(body)}")[:24],
            "title": chapter["title"], "body": body,
            "source_start": cursor, "source_end": cursor + len(body),
            "source_hash": text_hash(body), "characters": len(body),
            "non_whitespace_characters": len(re.sub(r"\s", "", body)),
            "empty_content": not body.strip("\ufeff \t\r\n"), "heading_in_body": False,
        })
        cursor += len(body)
    project = payload.get("project")
    title = project.get("title") if isinstance(project, dict) else None
    return _preview(
        raw, filename, fmt, encoding, chapters,
        warnings + ["已核对本应用的原文清单；章名、正文、空格和换行按导出前原稿恢复。"],
        {"extraction": "moyu_exact_manuscript", "exact_decoded_text": True,
         "manuscript_text_roundtrip": True, "format_roundtrip": False,
         "manuscript_source_sha256": source_digest,
         **({"suggested_title": title[:200]} if isinstance(title, str) and title.strip() else {}),
         **dict(extra or {})},
    )


def _markdown_manuscript(text: str) -> tuple[Mapping[str, Any], str] | None:
    import moyu_publication

    claimed = list(re.finditer(r"<!--\s*moyu-manuscript-v1\b", text))
    if not claimed:
        return None
    markers = list(re.finditer(
        r"<!-- moyu-manuscript-v1 ([0-9a-f]{64}) ([A-Za-z0-9+/=]+) -->", text
    ))
    if len(claimed) != 1 or len(markers) != 1:
        raise ImportRejected("出版文件的原文清单损坏或重复，请重新导出文件。")
    try:
        payload = moyu_publication.markdown_source(text)
    except (AttributeError, TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise ImportRejected("出版文件的原文清单校验失败，请重新导出文件。") from exc
    if not isinstance(payload, dict):
        raise ImportRejected("出版文件的原文清单格式无效。")
    return payload, markers[0].group(1)


def _epub_manuscript(archive: zipfile.ZipFile, reading_count: int) -> tuple[Mapping[str, Any], str] | None:
    import moyu_publication

    manifest_path = "META-INF/moyu-export-manifest.json"
    names = set(archive.namelist())
    has_source = moyu_publication.SOURCE_PATH in names
    if manifest_path not in names:
        if has_source:
            raise ImportRejected("出版包缺少原文校验清单，请重新导出文件。")
        return None
    try:
        manifest = json.loads(_read_member(archive, manifest_path))
        if not isinstance(manifest, dict) or manifest.get("format") != "moyu-epub-v1":
            raise ImportRejected("出版包的原文校验清单格式无效。")
        reference = manifest.get("manuscript_source")
        if reference is None and not has_source and manifest.get("manifest_version", 1) == 1:
            return None
        if (manifest.get("manifest_version") != 2 or not isinstance(reference, dict) or
                reference.get("path") != moyu_publication.SOURCE_PATH or
                not isinstance(reference.get("sha256"), str) or
                re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None or not has_source):
            raise ImportRejected("出版包的原文地址或校验值无效，请重新导出文件。")
        payload = moyu_publication.decode_source(
            _read_member(archive, moyu_publication.SOURCE_PATH), reference["sha256"]
        )
        if not isinstance(payload, dict):
            raise ImportRejected("出版包的原文清单格式无效。")
        chapters = payload["chapters"]
        entries = manifest.get("chapters")
        count = manifest.get("chapter_count")
        if (isinstance(count, bool) or not isinstance(count, int) or
                count != len(chapters) or count != reading_count or
                not isinstance(entries, list) or len(entries) != count):
            raise ImportRejected("出版包的章节数量与原文清单不一致。")
        for chapter, entry in zip(chapters, entries):
            if (not isinstance(entry, dict) or entry.get("title") != chapter["title"] or
                    entry.get("body_sha256") != text_hash(chapter["body"]) or
                    isinstance(entry.get("body_codepoints"), bool) or
                    not isinstance(entry.get("body_codepoints"), int) or
                    entry["body_codepoints"] != len(chapter["body"])):
                raise ImportRejected("出版包的章名、正文长度或校验值不一致。")
    except (AttributeError, TypeError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ImportRejected):
            raise
        raise ImportRejected("出版包的原文清单校验失败，请重新导出文件。") from exc
    return payload, reference["sha256"]


def _parse_epub(raw: bytes, filename: str, digest: str) -> dict[str, Any]:
    warnings = ["EPUB 只迁入文字与章节顺序；版式、样式、字体、图片和交互不会成为可编辑正文，原文件仍会保存。"]
    with _safe_archive(raw) as archive:
        container = _xml(_read_member(archive, "META-INF/container.xml"), "容器目录")
        if container.tag != "{" + _CONTAINER + "}container":
            raise ImportRejected("EPUB 容器目录格式不正确。")
        roots = container.findall("{" + _CONTAINER + "}rootfiles/{" + _CONTAINER + "}rootfile")
        supported = [item for item in roots if item.get("media-type") == "application/oebps-package+xml"]
        if not supported:
            raise ImportRejected("EPUB 没有可读取的书籍目录。")
        opf_path = _zip_member_name(supported[0].get("full-path", ""))
        if len(supported) > 1:
            warnings.append("书籍包含多个阅读版本，本次读取容器列出的第一个版本。")
        package = _xml(_read_member(archive, opf_path), "书籍目录")
        if package.tag != "{" + _OPF + "}package" or package.get("version") not in {"2.0", "3.0"}:
            raise ImportRejected("仅支持 EPUB 2 或 EPUB 3 的标准书籍目录。")
        manifest, spine = package.find("{" + _OPF + "}manifest"), package.find("{" + _OPF + "}spine")
        if manifest is None or spine is None:
            raise ImportRejected("EPUB 缺少正文清单或阅读顺序。")
        items: dict[str, dict[str, str]] = {}
        for item in manifest:
            if _local_name(item) != "item":
                continue
            item_id = item.get("id", "")
            if not item_id or item_id in items:
                raise ImportRejected("EPUB 正文清单含有空编号或重复编号。")
            items[item_id] = {
                "href": item.get("href", ""), "media_type": item.get("media-type", ""),
                "properties": item.get("properties", ""),
            }
        reading: list[tuple[str, bool]] = []
        used = set()
        for itemref in spine:
            if _local_name(itemref) != "itemref":
                continue
            item_id = itemref.get("idref", "")
            if item_id not in items:
                raise ImportRejected("EPUB 的阅读顺序引用了不存在的正文。")
            reading.append((item_id, False))
            used.add(item_id)
        if not reading:
            raise ImportRejected("EPUB 没有可导入的正文。")
        for item_id, item in items.items():
            if item_id not in used and item["media_type"] == "application/xhtml+xml":
                if "nav" in item["properties"].split():
                    warnings.append("独立导航目录保留在原文件中，没有重复加入章节正文。")
                else:
                    reading.append((item_id, True))
                    warnings.append("未列入阅读顺序的附属文字已追加保留，请在预览中确认脚注或附录。")
        title = next((item.text for item in package.iter() if _local_name(item) == "title" and item.text), None)
        chapters, cursor = [], 0
        for item_id, supplemental in reading:
            item = items[item_id]
            if item["media_type"] != "application/xhtml+xml":
                raise ImportRejected("EPUB 包含不支持的正文类型，无法保证文字完整；请改用 TXT 或 Markdown。")
            path = _resolve_member(opf_path, item["href"])
            content_raw = _read_member(archive, path)
            document = _xml(content_raw, "章节正文")
            if _local_name(document) != "html":
                raise ImportRejected("EPUB 的章节不是完整的 XHTML 正文。")
            body = next((element for element in document if _local_name(element) == "body"), None)
            if body is None:
                raise ImportRejected("EPUB 的章节缺少正文区域。")
            text = _visible_text(body)
            heading = next(
                ("".join(element.itertext()).strip() for element in body.iter() if _local_name(element) in {"h1", "h2", "h3"} and "".join(element.itertext()).strip()),
                None,
            )
            if any(_local_name(element) in {"img", "svg", "math", "audio", "video", "script", "style"} for element in body.iter()):
                warnings.append(f"“{path}”含有版式或媒体内容；图片说明与可读文字已保留，请对照原文件确认。")
            chapter_title = heading or posixpath.splitext(posixpath.basename(path))[0] or f"导入章节 {len(chapters) + 1}"
            if supplemental:
                chapter_title = "附属文字 · " + chapter_title
            sections, split_warnings = _split_text(text, chapter_title, digest + ":" + path + ":" + str(len(chapters)), False)
            # A spine item is already an author-defined chapter. Preserve it
            # unless the application's per-chapter bound requires splitting.
            if len(text) <= MAX_CHAPTER_CHARS:
                sections = [{
                    "id": "import_" + text_hash(f"{digest}:{path}:{len(chapters)}")[:24],
                    "title": chapter_title[:500], "body": text, "source_start": 0, "source_end": len(text),
                    "source_hash": text_hash(text), "characters": len(text),
                    "non_whitespace_characters": len(re.sub(r"\s", "", text)),
                    "empty_content": not text.strip(), "heading_in_body": bool(heading),
                }]
            else:
                warnings.extend(split_warnings)
            for section in sections:
                local_start, local_end = section["source_start"], section["source_end"]
                section.update({
                    "source_start": cursor + local_start, "source_end": cursor + local_end,
                    "source_member": path, "source_member_hash": _digest(content_raw),
                    "source_member_text_hash": text_hash(text),
                    "source_member_start": local_start, "source_member_end": local_end,
                    "supplemental": supplemental,
                })
                chapters.append(section)
            cursor += len(text)
        exact = _epub_manuscript(archive, len(reading))
        if exact is not None:
            return _exact_preview(
                raw, filename, "epub", "utf-8 manuscript JSON",
                exact[0], exact[1], warnings, {"package_path": opf_path},
            )
    return _preview(
        raw, filename, "epub", "utf-8/utf-16 XHTML", chapters, warnings,
        {"suggested_title": str(title or posixpath.splitext(filename)[0])[:200],
         "extraction": "visible_xhtml_text", "format_roundtrip": False,
         "package_path": opf_path},
    )


def _preview(raw: bytes, filename: str, fmt: str, encoding: str, chapters: list[dict[str, Any]],
             warnings: list[str], extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not chapters or len(chapters) > MAX_CHAPTERS:
        raise ImportRejected("章节数为空或超过 2000 章，请先拆分文件。")
    text = "".join(chapter["body"] for chapter in chapters)
    metadata = {
        "format": fmt, "encoding": encoding, "source_bytes": len(raw),
        "chapter_count": len(chapters), "characters": len(text),
        "non_whitespace_characters": len(re.sub(r"\s", "", text)),
        "text_sha256": text_hash(text), "offset_unit": "unicode_codepoint",
        "exact_decoded_text": fmt in {"txt", "md"},
        "suggested_title": posixpath.splitext(filename)[0][:200] or "导入作品",
        **dict(extra or {}),
    }
    if any(chapter["empty_content"] for chapter in chapters):
        warnings.append("预览中包含空正文或只有标题的章节，已保留，没有自动删除。")
    titles = [chapter["title"] for chapter in chapters]
    if len(set(titles)) != len(titles):
        warnings.append("出现同名章节，各章已使用独立编号保留，没有合并。")
    return {
        "filename": filename, "source_sha256": _digest(raw),
        "chapters": chapters, "warnings": list(dict.fromkeys(warnings)), "metadata": metadata,
    }


def parse_import(rawbytes: bytes, filename: str) -> dict[str, Any]:
    """Return a loss-reporting preview, never write or call a model."""
    if not isinstance(rawbytes, bytes) or not rawbytes or len(rawbytes) > MAX_SOURCE_BYTES:
        raise ImportRejected("请选择非空文件，原文件最多 32 MB。")
    filename = _filename(filename)
    suffix = posixpath.splitext(filename)[1].casefold()
    if suffix not in {".txt", ".md", ".markdown", ".epub"}:
        raise ImportRejected("目前支持 TXT、Markdown 和 EPUB 文件。")
    try:
        if suffix == ".epub":
            return _parse_epub(rawbytes, filename, _digest(rawbytes))
        text, encoding, warnings = _decode_text(rawbytes)
        if not text.strip("\ufeff \t\r\n"):
            raise ImportRejected("文件没有可读取的文字。")
        if suffix != ".txt":
            exact = _markdown_manuscript(text)
            if exact is not None:
                return _exact_preview(rawbytes, filename, "md", encoding, exact[0], exact[1], warnings)
        chapters, split_warnings = _split_text(text, filename, _digest(rawbytes), suffix != ".txt")
        return _preview(rawbytes, filename, "txt" if suffix == ".txt" else "md", encoding, chapters, warnings + split_warnings)
    except ImportRejected:
        raise
    except (UnicodeError, zipfile.BadZipFile, OSError, RuntimeError, ET.ParseError, ValueError) as exc:
        raise ImportRejected("文件格式损坏，无法安全导入；当前作品没有改动。") from exc


def validate_import_selection(parsed: Mapping[str, Any], data: Mapping[str, Any]) -> dict[str, Any]:
    """Allow chapter retitling/splitting while requiring all source text in order."""
    if not isinstance(parsed, Mapping) or not isinstance(data, Mapping):
        raise ImportRejected("导入确认格式错误。")
    if data.get("source_sha256") != parsed.get("source_sha256"):
        raise ImportRejected("预览对应的原文件已经改变，请重新选择文件。")
    mode = data.get("mode", "manuscript")
    if mode not in {"project", "manuscript", "reference"}:
        raise ImportRejected("请选择导入新作品或加入参考库。")
    if mode == "project":
        mode = "manuscript"
    title = data.get("title") or parsed["metadata"]["suggested_title"]
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ImportRejected("作品或参考书名称需要为 1 到 200 个字。")
    source = parsed.get("chapters", [])
    chapters = data.get("chapters", source)
    if not isinstance(chapters, list) or not chapters or len(chapters) > MAX_CHAPTERS:
        raise ImportRejected("需要保留 1 到 2000 个章节。")
    validated, cursor = [], 0
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, Mapping):
            raise ImportRejected("章节格式错误。")
        chapter_title, body = chapter.get("title"), chapter.get("body")
        if not isinstance(chapter_title, str) or not chapter_title.strip() or len(chapter_title) > 500:
            raise ImportRejected("每章名称需要为 1 到 500 个字；同名章节可以保留。")
        if not isinstance(body, str) or len(body) > MAX_CHAPTER_CHARS:
            raise ImportRejected("每章正文最多 200 万字，请调整分章。")
        item = {
            "id": "import_" + text_hash(f"{parsed['source_sha256']}:{index}:{cursor}:{len(body)}")[:24],
            "title": chapter_title.strip(), "body": body,
            "source_start": cursor, "source_end": cursor + len(body),
            "source_hash": text_hash(body), "characters": len(body),
            "non_whitespace_characters": len(re.sub(r"\s", "", body)),
        }
        origins = []
        for original in source:
            left, right = original["source_start"], original["source_end"]
            overlap_left, overlap_right = max(left, cursor), min(right, cursor + len(body))
            if overlap_left < overlap_right:
                origins.append({
                    "preview_chapter_id": original["id"],
                    "source_start": overlap_left, "source_end": overlap_right,
                    **{key: original[key] for key in ("source_member", "source_member_hash", "source_member_text_hash") if key in original},
                })
        item["sources"] = origins
        validated.append(item)
        cursor += len(body)
    text = "".join(item["body"] for item in validated)
    original = "".join(item["body"] for item in source)
    if text != original or text_hash(text) != parsed["metadata"]["text_sha256"]:
        raise ImportRejected("拆章后原文有缺失、重复、改字或顺序改变；请保留完整原文，导入后再编辑正文。")
    return {
        "mode": mode, "title": title.strip(), "filename": parsed["filename"],
        "source_sha256": parsed["source_sha256"], "chapters": validated,
        "metadata": copy.deepcopy(parsed["metadata"]), "warnings": list(parsed.get("warnings", [])),
    }


def ensure_reference_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS app_manuscript_import(
        id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES project(id),
        branch_id TEXT NOT NULL REFERENCES branch(id),filename TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,source_bytes BLOB NOT NULL,text_sha256 TEXT NOT NULL,
        metadata_json TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(project_id,branch_id,source_sha256))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_reference_document(
        id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES project(id),
        branch_id TEXT NOT NULL REFERENCES branch(id),filename TEXT NOT NULL,title TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,source_bytes BLOB NOT NULL,text_sha256 TEXT NOT NULL,
        metadata_json TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(project_id,branch_id,source_sha256))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_reference_section(
        id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES app_reference_document(id),
        project_id TEXT NOT NULL REFERENCES project(id),branch_id TEXT NOT NULL REFERENCES branch(id),
        ordinal INTEGER NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,source_json TEXT NOT NULL)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS app_reference_section_scope
        ON app_reference_section(project_id,branch_id,document_id,ordinal)""")


def _scope(scope: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(scope, Mapping):
        raise ImportRejected("参考库需要明确所属作品。")
    project, branch = scope.get("project_id"), scope.get("branch_id")
    if not isinstance(project, str) or not project or not isinstance(branch, str) or not branch:
        raise ImportRejected("参考库需要明确所属作品和分支。")
    return project, branch


def _require_write_scope(conn: sqlite3.Connection, project: str, branch: str) -> None:
    if conn.execute("SELECT 1 FROM branch WHERE id=? AND project_id=?", (branch, project)).fetchone() is None:
        raise ImportRejected("导入所属作品和分支不一致。")


@contextmanager
def _atomic(conn: sqlite3.Connection):
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    name = "reference_" + uuid.uuid4().hex
    conn.execute("SAVEPOINT " + name)
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO " + name)
        conn.execute("RELEASE " + name)
        raise
    else:
        conn.execute("RELEASE " + name)


def _reference_record(row: Any) -> dict[str, Any]:
    return {
        "id": row[0], "project_id": row[1], "branch_id": row[2], "filename": row[3],
        "title": row[4], "source_sha256": row[5], "text_sha256": row[6],
        "metadata": json.loads(row[7]), "created_at": row[8],
        "material_kind": "reference", "use": "reference_not_story_fact",
    }


def list_references(conn: sqlite3.Connection, scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    ensure_reference_tables(conn)
    project, branch = _scope(scope)
    return [_reference_record(row) for row in conn.execute(
        "SELECT id,project_id,branch_id,filename,title,source_sha256,text_sha256,metadata_json,created_at "
        "FROM app_reference_document WHERE project_id=? AND branch_id=? ORDER BY rowid DESC",
        (project, branch),
    ).fetchall()]


def list_import_sources(conn: sqlite3.Connection, scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    ensure_reference_tables(conn)
    project, branch = _scope(scope)
    return [{
        "id": row[0], "project_id": row[1], "branch_id": row[2], "filename": row[3],
        "source_sha256": row[4], "text_sha256": row[5], "metadata": json.loads(row[6]),
        "created_at": row[7],
    } for row in conn.execute(
        "SELECT id,project_id,branch_id,filename,source_sha256,text_sha256,metadata_json,created_at "
        "FROM app_manuscript_import WHERE project_id=? AND branch_id=? ORDER BY rowid DESC",
        (project, branch),
    ).fetchall()]


def save_import_source(conn: sqlite3.Connection, scope: Mapping[str, Any], parsed: Mapping[str, Any],
                       rawbytes: bytes) -> dict[str, Any]:
    """Archive an exact manuscript original, leaving the outer commit to caller."""
    project, branch = _scope(scope)
    _require_write_scope(conn, project, branch)
    if not isinstance(rawbytes, bytes) or _digest(rawbytes) != parsed.get("source_sha256"):
        raise ImportRejected("导入原文件校验失败。")
    ensure_reference_tables(conn)
    with _atomic(conn):
        existing = next((item for item in list_import_sources(conn, scope) if item["source_sha256"] == parsed["source_sha256"]), None)
        if existing:
            return {**existing, "reused": True}
        item_id, stamp = "import_source_" + uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
        metadata = {**parsed["metadata"], "warnings": list(parsed.get("warnings", []))}
        conn.execute(
            "INSERT INTO app_manuscript_import VALUES(?,?,?,?,?,?,?,?,?)",
            (item_id, project, branch, parsed["filename"], parsed["source_sha256"], rawbytes,
             parsed["metadata"]["text_sha256"], _json(metadata), stamp),
        )
        return next(item for item in list_import_sources(conn, scope) if item["id"] == item_id)


def reference_details(conn: sqlite3.Connection, scope: Mapping[str, Any], document_id: str) -> dict[str, Any]:
    """Read a reference's parsed chapters; never expose its source BLOB."""
    project, branch = _scope(scope)
    document = next((item for item in list_references(conn, scope) if item["id"] == document_id), None)
    if document is None:
        raise ImportRejected("参考书不存在或不属于当前作品。")
    sections = [{
        "id": row[0], "ordinal": row[1], "title": row[2], "body": row[3],
        "source": json.loads(row[4]), "material_kind": "reference", "use": "reference_not_story_fact",
    } for row in conn.execute(
        "SELECT id,ordinal,title,body,source_json FROM app_reference_section "
        "WHERE document_id=? AND project_id=? AND branch_id=? ORDER BY ordinal,rowid",
        (document_id, project, branch),
    ).fetchall()]
    return {**document, "chapters": sections}


def save_reference(conn: sqlite3.Connection, scope: Mapping[str, Any], parsed: Mapping[str, Any],
                   selection: Mapping[str, Any], rawbytes: bytes) -> dict[str, Any]:
    project, branch = _scope(scope)
    _require_write_scope(conn, project, branch)
    checked = validate_import_selection(parsed, selection)
    if checked["mode"] != "reference" or not isinstance(rawbytes, bytes) or _digest(rawbytes) != parsed["source_sha256"]:
        raise ImportRejected("参考库原文件校验失败。")
    ensure_reference_tables(conn)
    with _atomic(conn):
        existing = conn.execute(
            "SELECT id,project_id,branch_id,filename,title,source_sha256,text_sha256,metadata_json,created_at "
            "FROM app_reference_document WHERE project_id=? AND branch_id=? AND source_sha256=?",
            (project, branch, parsed["source_sha256"]),
        ).fetchone()
        if existing:
            return {**_reference_record(existing), "reused": True}
        document_id = "reference_" + uuid.uuid4().hex
        stamp = datetime.now(timezone.utc).isoformat()
        metadata = {**checked["metadata"], "warnings": checked["warnings"], "chapter_count": len(checked["chapters"])}
        conn.execute(
            "INSERT INTO app_reference_document VALUES(?,?,?,?,?,?,?,?,?,?)",
            (document_id, project, branch, checked["filename"], checked["title"], checked["source_sha256"],
             rawbytes, checked["metadata"]["text_sha256"], _json(metadata), stamp),
        )
        for index, chapter in enumerate(checked["chapters"]):
            section_id = "reference_section_" + text_hash(document_id + ":" + str(index))[:28]
            conn.execute(
                "INSERT INTO app_reference_section VALUES(?,?,?,?,?,?,?,?)",
                (section_id, document_id, project, branch, index, chapter["title"], chapter["body"],
                 _json({key: chapter[key] for key in ("source_start", "source_end", "source_hash", "sources")})),
            )
        return next(item for item in list_references(conn, scope) if item["id"] == document_id)


def search_references(conn: sqlite3.Connection, scope: Mapping[str, Any], query: str, *,
                      document_id: str | None = None, limit: int = 10,
                      records: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Return exact reference paragraphs, marked unsuitable as story facts."""
    ensure_reference_tables(conn)
    project, branch = _scope(scope)
    if not isinstance(query, str) or len(query) > 5000:
        raise ImportRejected("参考搜索文字过长或格式错误。")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 40:
        raise ImportRejected("参考搜索结果数应为 0 到 40。")
    if document_id is not None and (not isinstance(document_id, str) or not document_id):
        raise ImportRejected("参考书编号无效。")
    clause, params = "s.project_id=? AND s.branch_id=?", [project, branch]
    if document_id:
        clause += " AND s.document_id=?"
        params.append(document_id)
    count = conn.execute("SELECT COUNT(*) FROM app_reference_section s WHERE " + clause, params).fetchone()[0]
    rows = conn.execute(
        "SELECT s.id,s.document_id,s.title,s.body,s.source_json,d.filename,d.title,d.source_sha256 "
        "FROM app_reference_section s JOIN app_reference_document d ON d.id=s.document_id "
        "AND d.project_id=s.project_id AND d.branch_id=s.branch_id WHERE " + clause +
        " ORDER BY d.rowid DESC,s.ordinal LIMIT ?", (*params, MAX_SEARCH_SECTIONS),
    )
    materials, scanned, scanned_bytes = [], 0, 0
    for row in rows:
        section_id, doc_id, title, body, source_json, filename, document_title, digest = tuple(row)
        section_bytes = len(body.encode("utf-8"))
        if scanned_bytes + section_bytes > MAX_SEARCH_UTF8_BYTES:
            break
        scanned += 1
        scanned_bytes += section_bytes
        source = json.loads(source_json)
        for start, end in paragraph_ranges(body):
            quote = body[start:end]
            materials.append({
                "id": f"{section_id}:{start}:{end}", "project_id": project, "branch_id": branch,
                "kind": "reference", "material_kind": "reference", "use": "reference_not_story_fact",
                "title": title, "body": quote, "filename": filename, "document_title": document_title,
                "source_document_id": doc_id, "source_section_id": section_id,
                "source_start": source["source_start"] + start, "source_end": source["source_start"] + end,
                "section_start": start, "section_end": end, "source_quote": quote,
                "source_sha256": digest, "source_hash": text_hash(quote),
                "section_hash": text_hash(body), "offset_unit": "unicode_codepoint",
            })
    result = search_materials(materials, query, ScopePolicy(project, branch), records=records or [], limit=limit)
    result.update({
        "material_kind": "reference", "use": "reference_not_story_fact",
        "sections_considered": scanned, "total_sections": count,
        "source_utf8_bytes_considered": scanned_bytes,
        "limited": count > scanned,
        "warnings": ["参考库超过单次搜索范围，本次只检索已计入的完整章节，请缩小参考书范围或拆分大书。"] if count > scanned else [],
    })
    return result
