#!/usr/bin/env python3
"""Exercise codepoint-safe chapter patch preview/apply and its audit trail."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def request(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, method=method, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    database = tempfile.mktemp(prefix="moyu-range-patch-", suffix=".sqlite3")
    environment = {**os.environ, "MOYU_DB": database, "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(port)}
    process = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if request(base, "GET", "/api/health")[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.05)
        status, state = request(base, "GET", "/api/state")
        assert status == 200, state
        chapter = state["chapters"][0]
        chapter_id = chapter["id"]
        body = chapter["body"]
        revision = chapter["revision_id"]
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert chapter["revision_id"] == revision

        # Preview is read-only and addresses a Unicode string by codepoints.
        preview_payload = {
            "start": 0,
            "end": 2,
            "replacement": "写作",
            "base_revision_id": revision,
            "base_hash": content_hash,
            "expected_head": state["head_revision"],
        }
        status, preview = request(base, "POST", f"/api/chapters/{chapter_id}/patch", preview_payload)
        assert status == 200 and preview["applied"] is False, preview
        patch = preview["patch"]
        assert patch["range"] == {"start": 0, "end": 2}
        assert patch["result_hash"] == hashlib.sha256(("写作" + body[2:]).encode("utf-8")).hexdigest()
        status, unchanged = request(base, "GET", "/api/state")
        assert status == 200 and unchanged["chapters"][0]["body"] == body

        # A hash from another base must be rejected before any mutation.
        bad = dict(preview_payload)
        bad["base_hash"] = "0" * 64
        status, conflict = request(base, "POST", f"/api/chapters/{chapter_id}/patch", bad)
        assert status == 409 and conflict["error"] == "base hash mismatch", conflict

        # Apply the exact preview.  This creates a new immutable revision and
        # an append-only chapter.range_patched event.
        apply_payload = {**preview_payload, "apply": True}
        status, applied = request(base, "POST", f"/api/chapters/{chapter_id}/patch", apply_payload)
        assert status == 201 and applied["applied"] is True, applied
        new_revision = applied["revision_id"]
        assert new_revision == applied["patch"]["new_revision_id"]
        assert applied["event_id"] == applied["patch"]["event_id"]

        status, after = request(base, "GET", "/api/state")
        assert status == 200
        chapter_after = next(item for item in after["chapters"] if item["id"] == chapter_id)
        assert chapter_after["revision_id"] == new_revision
        assert chapter_after["body"] == "写作" + body[2:]
        assert after["head_revision"] == new_revision

        # The old base remains immutable and cannot be applied again.
        stale = {**apply_payload, "expected_head": after["head_revision"]}
        status, stale_result = request(base, "POST", f"/api/chapters/{chapter_id}/patch", stale)
        assert status == 409 and stale_result["error"] == "base revision mismatch", stale_result

        status, revisions = request(base, "GET", f"/api/chapters/{chapter_id}/revisions")
        assert status == 200
        latest = next(item for item in revisions["revisions"] if item["id"] == new_revision)
        assert latest["operation"] == "range_patch"
        status, events = request(base, "GET", "/api/events?limit=30")
        assert status == 200
        event = next(item for item in events["events"] if item["id"] == applied["event_id"])
        assert event["event_type"] == "chapter.range_patched"
        assert event["payload_json"]["patch"]["range"] == {"start": 0, "end": 2}

        # Off-by-one and non-integer ranges never reach the revision writer.
        invalid = {"start": 0, "end": len(chapter_after["body"]) + 1, "replacement": "x", "base_hash": chapter_after["revision_id"]}
        status, _ = request(base, "POST", f"/api/chapters/{chapter_id}/patch", invalid)
        assert status == 400
        invalid = {"start": 0.5, "end": 1, "replacement": "x", "base_hash": chapter_after["revision_id"]}
        status, _ = request(base, "POST", f"/api/chapters/{chapter_id}/patch", invalid)
        assert status == 400
    finally:
        process.terminate()
        process.wait(timeout=3)
    print("range patch fixtures: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
