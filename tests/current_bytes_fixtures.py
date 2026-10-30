#!/usr/bin/env python3
"""Checks for the current-bytes manifest and drift checker."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_manifest import DEFAULT_MANIFEST, check_manifest  # noqa: E402


def check(condition: bool, label: str, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed{': ' + detail if detail else ''}")
    print(f"ok - {label}")


def main() -> int:
    payload = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    files = payload["files"]
    check(len(files) >= 10, "manifest covers critical bytes", str(len(files)))
    check(len({item["path"] for item in files}) == len(files), "manifest has unique paths")
    check(check_manifest() == [], "current tree matches manifest")

    target = files[0]
    target_path = ROOT / target["path"]
    data = target_path.read_bytes()
    wrong = hashlib.sha256(data + b"\n").hexdigest()
    with tempfile.TemporaryDirectory(prefix="moyu-manifest-") as temp:
        bad = {
            "version": 1,
            "files": [
                {
                    "path": target["path"],
                    "lines": target["lines"],
                    "sha256": wrong,
                }
            ],
        }
        bad_path = Path(temp) / "manifest.json"
        bad_path.write_text(json.dumps(bad), encoding="utf-8")
        errors = check_manifest(bad_path)
        check(any("hash drift" in error for error in errors), "wrong hash is rejected", str(errors))

    with tempfile.TemporaryDirectory(prefix="moyu-manifest-") as temp:
        missing = {
            "version": 1,
            "files": [{"path": "docs/__missing__.md", "lines": 0, "sha256": "0" * 64}],
        }
        missing_path = Path(temp) / "manifest.json"
        missing_path.write_text(json.dumps(missing), encoding="utf-8")
        errors = check_manifest(missing_path)
        check(any("missing file" in error for error in errors), "missing file is rejected", str(errors))

    print(f"current bytes fixtures: 5 checks ok ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
