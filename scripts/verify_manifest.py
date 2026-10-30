#!/usr/bin/env python3
"""Verify that a current-bytes manifest still matches the working tree.

A content-bound verdict may only be reused while the input bytes carry the
same sha256.  This checker turns that rule into an executable gate: any drift
is reported as a hard error instead of silently treating an old verdict as a
current pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "current-bytes-manifest-2026-10-30.json"


def check_manifest(manifest_path: Path | str = DEFAULT_MANIFEST) -> list[str]:
    path = Path(manifest_path)
    if not path.is_absolute():
        path = ROOT / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest unreadable: {path}: {exc}"]

    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(payload.get("files", [])):
        if not isinstance(item, dict):
            errors.append(f"files[{index}] must be an object")
            continue
        rel = item.get("path")
        expected_hash = item.get("sha256")
        expected_lines = item.get("lines")
        if not isinstance(rel, str) or not rel:
            errors.append(f"files[{index}].path missing")
            continue
        if rel in seen:
            errors.append(f"duplicate path: {rel}")
            continue
        seen.add(rel)
        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:
            errors.append(f"path escapes workspace: {rel}")
            continue
        if not target.is_file():
            errors.append(f"missing file: {rel}")
            continue
        data = target.read_bytes()
        actual_hash = hashlib.sha256(data).hexdigest()
        actual_lines = data.count(b"\n")
        if actual_hash != expected_hash:
            errors.append(
                f"hash drift: {rel}: expected {expected_hash}, actual {actual_hash}"
            )
        if isinstance(expected_lines, int) and actual_lines != expected_lines:
            errors.append(
                f"line drift: {rel}: expected {expected_lines}, actual {actual_lines}"
            )
    if not payload.get("files"):
        errors.append("manifest has no files")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check current-bytes manifest")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args(argv)
    errors = check_manifest(Path(args.manifest))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"current bytes manifest: ok ({Path(args.manifest).name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
